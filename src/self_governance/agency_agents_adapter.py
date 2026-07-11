"""Agency Agents Adapter module.

Aligns with the msitarzewski/agency-agents schema to map role names to persona
structures and capabilities to specific prompt instructions.

Contains three sources of agent personas:
  - SDLC_PERSONA_REGISTRY: All 150+ agents from msitarzewski/agency-agents
    across Engineering, Design, Marketing, Sales, and Paid Media divisions.
  - COUNCIL_PERSONA_REGISTRY: 210+ pseudonymous real-world expert archetypes
    used in the Autonomous Council succession consensus tier.
  - DynamicAgentFactory: Synthesises new personas on-the-fly via LLM when a
    role is not found in either static registry.

The unified PERSONA_REGISTRY merges both static registries. ``get_persona``
falls through to DynamicAgentFactory and caches results for session reuse.
"""

from __future__ import annotations

import os
import json
import logging
from typing import Any, Dict, Optional

logger = logging.getLogger("self_governance.agency_agents_adapter")

# ---------------------------------------------------------------------------
# SDLC Building Agents — sourced from msitarzewski/agency-agents
# ---------------------------------------------------------------------------

def _load_and_validate_assets() -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Loads agents from the agents.json asset file on startup and validates their schemas."""
    sdlc_fallback: dict[str, dict[str, Any]] = {}
    council_fallback: dict[str, dict[str, Any]] = {}
    
    # Inline fallback representations for key roles
    sdlc_fallback["Backend Wizard"] = {
        "role": "Backend Wizard",
        "division": "Engineering",
        "emoji": "🧙",
        "vibe": "Magical full-stack backend wizardry — any language, any pattern, any scale.",
        "description": "General backend implementation wizardry.",
        "prompt": "You are a Backend Wizard. You write clean, performant backend code in any language.",
    }
    sdlc_fallback["QA Specialist"] = {
        "role": "QA Specialist",
        "division": "Engineering",
        "emoji": "✅",
        "vibe": "Edge cases, boundary conditions, and the tests nobody else writes.",
        "description": "Comprehensive quality assurance, edge-case coverage, and boundary validation.",
        "prompt": "You are a QA Specialist. You write comprehensive unit and integration tests.",
    }
    sdlc_fallback["Security Auditor"] = {
        "role": "Security Auditor",
        "division": "Engineering",
        "emoji": "🔐",
        "vibe": "Proactively hunts vulnerabilities before attackers do.",
        "description": "Application security hardening.",
        "prompt": "You are a Security Auditor. You audit implementations for security issues.",
        "quality_gate": {
            "min_confidence": 8.0,
            "require_evidence": False,
            "false_positive_exclusions": [
                "test mock", "unit test fixture", "intentionally vulnerable"
            ],
        },
    }
    
    council_fallback["Software Industry Visionary"] = {
        "role": "Software Industry Visionary",
        "division": "Council",
        "emoji": "🖥️",
        "vibe": "Philanthropy meets platform strategy — long-term thinking at civilisational scale.",
        "description": "Archetype: technology philanthropist and platform strategist.",
        "prompt": "You are a Software Industry Visionary. You push for long-term compounding value.",
    }
    
    import os
    import json
    this_dir = os.path.dirname(os.path.abspath(__file__))
    assets_file = os.path.join(this_dir, "assets", "agents.json")
    
    if not os.path.exists(assets_file):
        logger.warning("Asset file %s not found. Using inline fallback registries.", assets_file)
        return sdlc_fallback, council_fallback
        
    try:
        with open(assets_file, "r", encoding="utf-8") as f:
            data = json.load(f)
            
        loaded_sdlc = {}
        loaded_council = {}
        
        # Schema validation logic
        def validate_agent(role_name: str, val: Any) -> bool:
            if not isinstance(val, dict):
                return False
            # Check required fields
            if "role" not in val or not isinstance(val["role"], str) or not val["role"]:
                return False
            if "prompt" not in val or not isinstance(val["prompt"], str) or not val["prompt"]:
                return False
            # Optional fields checks
            if "capabilities" in val and not isinstance(val["capabilities"], list):
                return False
            if "developer_message" in val and val["developer_message"] is not None and not isinstance(val["developer_message"], str):
                return False
            return True
            
        for role, val in data.get("sdlc", {}).items():
            if validate_agent(role, val):
                loaded_sdlc[role] = val
            else:
                logger.warning("Validation failed for SDLC agent '%s'", role)
                
        for role, val in data.get("council", {}).items():
            if validate_agent(role, val):
                loaded_council[role] = val
            else:
                logger.warning("Validation failed for Council agent '%s'", role)
                
        if loaded_sdlc or loaded_council:
            # Successfully loaded assets
            return loaded_sdlc, loaded_council
            
    except Exception as e:
        logger.error("Error reading/validating agents.json asset: %s", e)
        
    return sdlc_fallback, council_fallback

SDLC_PERSONA_REGISTRY, COUNCIL_PERSONA_REGISTRY = _load_and_validate_assets()

PERSONA_REGISTRY: Dict[str, Dict[str, Any]] = {
    **SDLC_PERSONA_REGISTRY,
    **COUNCIL_PERSONA_REGISTRY,
}

# ---------------------------------------------------------------------------
# Capability registry (unchanged from original)
# ---------------------------------------------------------------------------

CAPABILITY_REGISTRY: Dict[str, str] = {
    "sqlite_concurrency": "Protect SQLite connections against write lock contentions under concurrent threading; commit transactions quickly.",
    "hmac_verification": "Verify webhook signatures using timing-attack safe compare_digest.",
    "path_traversal_hardening": "Strictly reject path traversal payloads (e.g., check for '../' or directory escapes).",
    "pytest_coverage": "Every new feature or fix must have unit or integration tests targeting 100% coverage.",
}


# ---------------------------------------------------------------------------
# Dynamic Agent Factory
# ---------------------------------------------------------------------------

class DynamicAgentFactory:
    """Synthesises new agent personas on-the-fly via LLM when a role is not
    found in the static PERSONA_REGISTRY.

    Results are cached in-process (in PERSONA_REGISTRY) so repeated requests
    for the same role name within a session do not incur redundant API calls.
    """

    # Prompt templates ---------------------------------------------------

    _SDLC_SYNTHESIS_PROMPT = (
        "You are configuring a new AI agent for a software engineering organisation.\n"
        "Synthesise a detailed persona for the following role:\n\n"
        "Role Name: {role_name}\n"
        "Task Context: {task_context}\n\n"
        "Return a JSON object with exactly these fields:\n"
        "  role              (string): the role name as given\n"
        "  division          (string): one of Engineering, Design, Marketing, Sales, Paid Media, General\n"
        "  emoji             (string): a single relevant emoji\n"
        "  vibe              (string): one punchy sentence describing this agent's approach\n"
        "  description       (string): 1-2 sentences on what this agent does and when to use it\n"
        "  prompt            (string): detailed system prompt for this agent, formatted using XML tags (<role_definition>, <gatekeeper_rules>, <capabilities>). IMPORTANT: Place the actual role identity and style at the very end of the prompt inside an <identity> tag.\n"
        "  developer_message (string): a stripped-down version of the prompt tailored for reasoning models (e.g., o1/o3), omitting extensive formatting and focusing purely on objective facts and constraints.\n"
    )

    _COUNCIL_SYNTHESIS_PROMPT = (
        "You are configuring a new Autonomous Council expert for a self-governing AI organisation.\n"
        "The council uses pseudonymous real-world expert archetypes — named by domain, not by person.\n\n"
        "Domain of Expertise: {domain}\n"
        "Project Context: {project_context}\n\n"
        "Think of a well-known public figure who is the world's foremost expert in this domain. "
        "Model the persona on their publicly known worldview, mental models, communication style, "
        "and decision-making heuristics — but name the role by domain only, not by person.\n\n"
        "Return a JSON object with exactly these fields:\n"
        "  role              (string): pseudonymous domain-based role name (e.g. 'Quantum Computing Researcher')\n"
        "  division          (string): always 'Council'\n"
        "  emoji             (string): a single relevant emoji\n"
        "  vibe              (string): one punchy sentence capturing this expert's worldview\n"
        "  description       (string): 1-2 sentences including the real-world archetype inspiration\n"
        "  prompt            (string): detailed in-character system prompt for this council expert, formatted using XML tags (<role_definition>, <gatekeeper_rules>, <capabilities>). IMPORTANT: Inject the pseudonymous real-life expert identity into an <identity> tag at the very bottom of the prompt.\n"
        "  developer_message (string): a stripped-down version of the prompt tailored for reasoning models (e.g., o1/o3), focusing purely on objective facts, worldview constraints, and heuristics, without extensive XML formatting.\n"
    )

    _SYNTHESIS_SCHEMA = {
        "type": "OBJECT",
        "properties": {
            "role":              {"type": "STRING"},
            "division":          {"type": "STRING"},
            "emoji":             {"type": "STRING"},
            "vibe":              {"type": "STRING"},
            "description":       {"type": "STRING"},
            "prompt":            {"type": "STRING"},
            "developer_message": {"type": "STRING"},
        },
        "required": ["role", "division", "emoji", "vibe", "description", "prompt", "developer_message"],
    }

    def synthesize_sdlc_agent(
        self,
        role_name: str,
        adapter: Any,
        task_context: str = "general software engineering",
    ) -> Dict[str, Any]:
        """Synthesise and cache a new SDLC agent persona via LLM.

        Args:
            role_name: The desired role name (e.g. 'Rust Systems Engineer').
            adapter: A GeminiExecutionAdapter instance for LLM calls.
            task_context: Optional project/task description to tune the persona.

        Returns:
            A persona dict in the same schema as PERSONA_REGISTRY entries.
        """
        if role_name in PERSONA_REGISTRY:
            return PERSONA_REGISTRY[role_name]

        prompt = self._SDLC_SYNTHESIS_PROMPT.format(
            role_name=role_name,
            task_context=task_context,
        )
        persona = self._call_and_parse(role_name, prompt, adapter)
        PERSONA_REGISTRY[role_name] = persona
        logger.info("DynamicAgentFactory: synthesised SDLC agent '%s'", role_name)
        return persona

    def synthesize_council_expert(
        self,
        domain: str,
        adapter: Any,
        project_context: str = "technology and software product development",
        use_web_grounding: bool = False,
    ) -> Dict[str, Any]:
        """Synthesise and cache a new Council expert persona via LLM.

        Args:
            domain: The expert's domain (e.g. 'Renewable Energy Storage').
            adapter: A GeminiExecutionAdapter instance for LLM calls.
            project_context: Optional project description to focus the archetype.
            use_web_grounding: If True and the adapter supports it, enables
                Google Search grounding for higher-fidelity persona synthesis.

        Returns:
            A persona dict in the same schema as COUNCIL_PERSONA_REGISTRY entries.
        """
        role_key = f"{domain} Expert"
        if role_key in PERSONA_REGISTRY:
            return PERSONA_REGISTRY[role_key]

        prompt = self._COUNCIL_SYNTHESIS_PROMPT.format(
            domain=domain,
            project_context=project_context,
        )
        grounding_tool: Optional[dict[str, Any]] = None
        if use_web_grounding:
            try:
                grounding_tool = {"google_search": {}}
            except Exception:
                grounding_tool = None

        persona = self._call_and_parse(
            role_key, prompt, adapter, grounding_tool=grounding_tool
        )
        # Override division to always be Council
        persona["division"] = "Council"
        PERSONA_REGISTRY[role_key] = persona
        logger.info("DynamicAgentFactory: synthesised Council expert '%s'", role_key)
        return persona

    def _call_and_parse(
        self,
        role_name: str,
        prompt: str,
        adapter: Any,
        grounding_tool: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """Call LLM and parse the structured persona response.

        Falls back to a generic persona if the call fails or returns malformed JSON.
        """
        try:
            raw = adapter._call_gemini_and_track(
                prompt,
                response_schema=self._SYNTHESIS_SCHEMA,
                response_mime_type="application/json",
            )
            if raw:
                data = json.loads(raw)
                required = {"role", "division", "emoji", "vibe", "description", "prompt", "developer_message"}
                if required.issubset(data.keys()):
                    return data
        except Exception:
            logger.warning(
                "DynamicAgentFactory: failed to synthesise persona for '%s'; using generic fallback.",
                role_name,
                exc_info=True,
            )

        return _generic_persona(role_name)


# Module-level factory singleton
dynamic_factory = DynamicAgentFactory()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def get_persona(
    role_name: str,
    adapter: Optional[Any] = None,
    task_context: str = "general software engineering",
    registry_path: Optional[str] = None,
) -> Dict[str, Any]:
    """Retrieves a persona from the unified registry, synthesising on-the-fly if needed.

    Lookup order:
      1. Exact match in custom registry (if provided) or PERSONA_REGISTRY.
      2. Case-insensitive fuzzy match against registered role names.
      3. DynamicAgentFactory synthesis via LLM (only when adapter is provided).
      4. Generic fallback persona.

    Args:
        role_name: The name of the role to retrieve.
        adapter: Optional GeminiExecutionAdapter for dynamic synthesis fallback.
        task_context: Task description passed to the dynamic factory when used.
        registry_path: Optional path to a custom agents JSON file.

    Returns:
        A dictionary containing role metadata and prompt instructions.
    """
    registry = PERSONA_REGISTRY
    if registry_path and os.path.exists(registry_path):
        try:
            with open(registry_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    # Basic validation and extraction of nested sdlc/council structures
                    custom_registry = {}
                    
                    # Handle structured format (like default agents.json)
                    if "sdlc" in data or "council" in data:
                        for section in ("sdlc", "council"):
                            for k, v in data.get(section, {}).items():
                                if isinstance(v, dict) and "role" in v and "prompt" in v:
                                    custom_registry[k] = v
                    # Handle flat format (just a dict of agents)
                    else:
                        for k, v in data.items():
                            if isinstance(v, dict) and "role" in v and "prompt" in v:
                                custom_registry[k] = v
                                
                    if custom_registry:
                        registry = custom_registry
        except Exception as e:
            logger.warning("Failed to load custom registry from %s: %s", registry_path, e)

    # 1. Exact match
    if role_name in registry:
        return registry[role_name]

    # 2. Case-insensitive match
    lower_name = role_name.lower()
    for key, persona in registry.items():
        if key.lower() == lower_name:
            return persona

    # 3. Dynamic synthesis
    if adapter is not None:
        try:
            return dynamic_factory.synthesize_sdlc_agent(
                role_name, adapter, task_context
            )
        except Exception:
            logger.warning(
                "get_persona: dynamic synthesis failed for '%s'; using generic fallback.",
                role_name,
                exc_info=True,
            )

    # 4. Generic fallback
    return _generic_persona(role_name)


def get_capability_prompt(capability_name: str) -> str:
    """Retrieves the prompt chunk for a given capability.

    Args:
        capability_name: The identifier of the capability.

    Returns:
        The instructions string associated with the capability, or an
        empty string if the capability is not registered.
    """
    return CAPABILITY_REGISTRY.get(capability_name, "")


def list_sdlc_roles() -> list[str]:
    """Returns all registered SDLC role names, sorted alphabetically."""
    return sorted(SDLC_PERSONA_REGISTRY.keys())


def list_council_roles() -> list[str]:
    """Returns all registered Council expert role names, sorted alphabetically."""
    return sorted(COUNCIL_PERSONA_REGISTRY.keys())


def register_persona(role_name: str, persona: Dict[str, Any]) -> None:
    """Registers a custom persona into the unified PERSONA_REGISTRY.

    Useful for injecting dynamically synthesised personas or custom overrides
    without modifying module-level dicts directly.

    Args:
        role_name: The registry key for this persona.
        persona: A dict with at least: role, division, description, prompt.
    """
    PERSONA_REGISTRY[role_name] = persona


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _generic_persona(role_name: str) -> Dict[str, Any]:
    """Returns a minimal generic persona for unrecognised role names."""
    prompt_content = f"You are acting as the specialised role: {role_name}."
    return {
        "role": role_name,
        "division": "General",
        "emoji": "🤖",
        "vibe": f"Specialised worker for {role_name}.",
        "description": f"Specialised worker for {role_name}.",
        "prompt": f"<role_definition>\n{prompt_content}\n</role_definition>\n<identity>\n{prompt_content}\n</identity>",
        "developer_message": prompt_content,
    }
