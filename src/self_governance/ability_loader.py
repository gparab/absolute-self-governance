import os
import json
import logging
from typing import Dict, Any

try:
    import yaml
except ImportError:
    yaml = None  # type: ignore

logger = logging.getLogger("self_governance.ability_loader")


class AbilityLoader:
    """Dynamically scans and loads ability files (JSON or YAML) from a target directory."""

    def __init__(self, abilities_dir: str = "src/self_governance/abilities") -> None:
        self.abilities_dir = abilities_dir

    def scan_abilities(self) -> Dict[str, Dict[str, Any]]:
        """Scans the abilities directory for JSON and YAML files."""
        abilities: Dict[str, Dict[str, Any]] = {}
        if not os.path.exists(self.abilities_dir):
            return abilities
        for filename in os.listdir(self.abilities_dir):
            filepath = os.path.join(self.abilities_dir, filename)
            if not os.path.isfile(filepath):
                continue
            name, ext = os.path.splitext(filename)
            ext = ext.lower()
            if ext == ".json":
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = json.load(f)
                        abilities[name] = data
                except Exception as e:
                    logger.warning("Skipping unreadable ability file %s: %s", filepath, e)
            elif ext in (".yaml", ".yml") and yaml is not None:
                try:
                    with open(filepath, "r", encoding="utf-8") as f:
                        data = yaml.safe_load(f)
                        abilities[name] = data
                except Exception as e:
                    logger.warning("Skipping unreadable ability file %s: %s", filepath, e)
        return abilities

    def load_ability(self, ability_name: str, agent_or_context: Any) -> bool:
        """Loads instructions for an ability and appends them to the agent/context.

        Args:
            ability_name: Name of the ability file (without extension).
            agent_or_context: Agent object or dict context to modify.

        Returns:
            True if successfully loaded, False otherwise.
        """
        abilities = self.scan_abilities()
        canonical_name = ability_name
        ability_data = abilities.get(ability_name)
        if not ability_data:
            for k, v in abilities.items():
                if k.lower() == ability_name.lower():
                    ability_data = v
                    canonical_name = k
                    break
        if not ability_data:
            return False

        instructions = ability_data.get("instructions")
        if not instructions:
            return False

        # Check if already loaded to prevent duplicate loading
        already_loaded = False

        prompt_str = ""
        if hasattr(agent_or_context, "prompt"):
            prompt_str = agent_or_context.prompt or ""
        elif isinstance(agent_or_context, dict) and "prompt" in agent_or_context:
            prompt_str = agent_or_context.get("prompt") or ""

        if f"### Loaded Ability ({canonical_name}):" in prompt_str:
            already_loaded = True

        if hasattr(agent_or_context, "capabilities"):
            caps = getattr(agent_or_context, "capabilities")
            if caps is not None and canonical_name in caps:
                already_loaded = True
        elif isinstance(agent_or_context, dict) and "capabilities" in agent_or_context:
            caps = agent_or_context.get("capabilities")
            if isinstance(caps, list) and canonical_name in caps:
                already_loaded = True

        # If it's an Agent object (from models.py)
        if hasattr(agent_or_context, "prompt"):
            if not already_loaded:
                agent_or_context.prompt = f"{agent_or_context.prompt}\n\n### Loaded Ability ({canonical_name}):\n{instructions}"
            if hasattr(agent_or_context, "capabilities"):
                caps = getattr(agent_or_context, "capabilities")
                if caps is not None and canonical_name not in caps:
                    caps.append(canonical_name)
            return True
        elif isinstance(agent_or_context, dict) and "prompt" in agent_or_context:
            if not already_loaded:
                agent_or_context["prompt"] = f"{agent_or_context['prompt']}\n\n### Loaded Ability ({canonical_name}):\n{instructions}"
            if "capabilities" in agent_or_context:
                caps = agent_or_context["capabilities"]
                if isinstance(caps, list) and canonical_name not in caps:
                    caps.append(canonical_name)
            return True

        return False
