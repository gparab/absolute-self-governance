"""LLM Provider Abstraction module.

Provides a unified interface for calling different LLM APIs: Gemini
natively, or any OpenRouter-hosted model (Claude, GPT, Llama, etc.) via an
OpenRouter API key -- see get_provider().
"""

import json
import time
import urllib.request
import urllib.error
import logging
from abc import ABC, abstractmethod
from typing import Dict, Any, Optional
from self_governance.config import DEFAULT_MODEL, DEFAULT_OPENROUTER_MODEL, DRAFT_MODEL

logger = logging.getLogger("self_governance.providers")

class LLMProvider(ABC):
    @abstractmethod
    def generate_content(
        self,
        prompt: str,
        api_key: Optional[str],
        model: Optional[str] = None,
        system_instruction: Optional[str] = None,
        developer_message: Optional[str] = None,
        response_mime_type: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        max_output_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        is_reasoning: bool = False,
        grounding_tool: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Generate content from the LLM.

        Returns:
            Dict containing 'text', 'prompt_tokens', 'completion_tokens', 'finish_reason', and optionally 'error'.
        """
        pass

class GeminiProvider(LLMProvider):
    def generate_content(
        self,
        prompt: str,
        api_key: Optional[str],
        model: Optional[str] = None,
        system_instruction: Optional[str] = None,
        developer_message: Optional[str] = None,
        response_mime_type: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        max_output_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        is_reasoning: bool = False,
        grounding_tool: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        model_name = model or DEFAULT_MODEL
        if not is_reasoning:
            mn_lower = model_name.lower()
            is_reasoning = any(x in mn_lower for x in ("o1", "o3", "thinking", "reasoning"))

        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent"
        headers = {"Content-Type": "application/json", "x-goog-api-key": api_key or ""}
        
        data: Dict[str, Any] = {"contents": [{"parts": [{"text": prompt}]}]}
        instruction_text = developer_message if is_reasoning else system_instruction
        if instruction_text:
            data["systemInstruction"] = {"parts": [{"text": instruction_text}]}

        if response_mime_type or response_schema or max_output_tokens or (not is_reasoning and temperature is not None):
            gen_config: Dict[str, Any] = {}
            if response_mime_type:
                gen_config["responseMimeType"] = response_mime_type
            if response_schema:
                gen_config["responseSchema"] = response_schema
            if max_output_tokens:
                gen_config["maxOutputTokens"] = max_output_tokens
            if not is_reasoning and temperature is not None:
                gen_config["temperature"] = min(2.0, max(0.0, temperature))
            data["generationConfig"] = gen_config
            
        if grounding_tool:
            data["tools"] = [grounding_tool]

        return _execute_request(url, headers, data, parse_gemini_response)

class OpenRouterProvider(LLMProvider):
    """Routes through OpenRouter's unified, OpenAI-compatible API, giving
    access to Claude, GPT, Llama, and any other OpenRouter-hosted model
    through a single OpenRouter API key -- not Anthropic-specific despite
    this module's prior class name."""

    def generate_content(
        self,
        prompt: str,
        api_key: Optional[str],
        model: Optional[str] = None,
        system_instruction: Optional[str] = None,
        developer_message: Optional[str] = None,
        response_mime_type: Optional[str] = None,
        response_schema: Optional[Dict[str, Any]] = None,
        max_output_tokens: Optional[int] = None,
        temperature: Optional[float] = None,
        is_reasoning: bool = False,
        grounding_tool: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        model_name = model or DEFAULT_OPENROUTER_MODEL
        if not is_reasoning:
            mn_lower = model_name.lower()
            is_reasoning = any(x in mn_lower for x in ("o1", "o3", "thinking", "reasoning"))

        # Usually hits OpenRouter or a direct Anthropic shim
        url = "https://openrouter.ai/api/v1/chat/completions"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
            "HTTP-Referer": "https://github.com/gparab/absolute-self-governance",
            "X-Title": "Absolute Self-Governance",
        }
        messages = []
        if is_reasoning and developer_message:
            messages.append({"role": "developer", "content": developer_message})
        elif not is_reasoning and system_instruction:
            messages.append({"role": "system", "content": system_instruction})
        messages.append({"role": "user", "content": prompt})

        data: Dict[str, Any] = {
            "model": model_name,
            "messages": messages,
        }
        if not is_reasoning and temperature is not None:
            data["temperature"] = min(2.0, max(0.0, temperature))
        if response_mime_type == "application/json" or response_schema:
            data["response_format"] = {"type": "json_object"}
            
        return _execute_request(url, headers, data, parse_openrouter_response)

def parse_gemini_response(res_data: Dict[str, Any]) -> Dict[str, Any]:
    candidates = res_data.get("candidates", [])
    usage_metadata = res_data.get("usageMetadata", {})
    prompt_tokens = usage_metadata.get("promptTokenCount", 0)
    completion_tokens = usage_metadata.get("candidatesTokenCount", 0)

    text = ""
    finish_reason = "STOP"
    if candidates:
        finish_reason = candidates[0].get("finishReason", "STOP")
        content = candidates[0].get("content", {})
        parts = content.get("parts", [])
        if parts:
            text = parts[0].get("text", "").strip()

    return {
        "text": text,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "finish_reason": finish_reason,
    }

def parse_openrouter_response(res_data: Dict[str, Any]) -> Dict[str, Any]:
    choices = res_data.get("choices", [])
    usage_metadata = res_data.get("usage", {})
    prompt_tokens = usage_metadata.get("prompt_tokens", 0)
    completion_tokens = usage_metadata.get("completion_tokens", 0)
    
    text = ""
    finish_reason = "STOP"
    if choices:
        finish_reason = choices[0].get("finish_reason", "STOP")
        if finish_reason is None:
            finish_reason = "STOP"
        else:
            finish_reason = str(finish_reason).upper()
        text = choices[0].get("message", {}).get("content", "").strip()

    return {
        "text": text,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "finish_reason": finish_reason,
    }

def _execute_request(url: str, headers: Dict[str, str], data: Dict[str, Any], parser_func: Any) -> Dict[str, Any]:
    attempts = 3
    delay = 1.0

    for attempt in range(attempts):
        req = urllib.request.Request(
            url, data=json.dumps(data).encode(), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as response:  # nosec B310
                res_data = json.loads(response.read().decode())
                return parser_func(res_data)
        except urllib.error.HTTPError as he:
            if he.code in (429, 500, 502, 503, 504) and attempt < attempts - 1:
                logger.warning(
                    "API returned transient error %s. Retrying in %s seconds...",
                    he.code,
                    delay,
                )
                time.sleep(delay)
                delay *= 2.0
            else:
                logger.error("API HTTP Error %s: %s", he.code, he.read().decode())
                break
        except Exception as e:
            if attempt < attempts - 1:
                logger.warning("Query error: %s. Retrying in %s seconds...", e, delay)
                time.sleep(delay)
                delay *= 2.0
            else:
                logger.error("Failed to query API: %s", e)
                break

    return {
        "text": "",
        "prompt_tokens": 0,
        "completion_tokens": 0,
        "finish_reason": "ERROR",
        "error": True,
    }

def _self_consistency_uncertainty(
    provider: LLMProvider, prompt: str, api_key: Optional[str], draft_model: str, **kwargs: Any
) -> float:
    """Cheap uncertainty proxy (uncertainty-based two-tier selection,
    research.google survey, July 2026 topic-page batch): the paper's own
    method uses the draft model's token-level logprob entropy, which
    Gemini/OpenRouter don't reliably expose through ASG's existing
    providers -- so this substitutes a self-consistency proxy, sampling
    the draft model twice and measuring how much the two responses agree
    (via the same Jaccard token-overlap already used for groupthink
    detection in consensus.py). Low agreement stands in for high
    uncertainty.

    **kwargs (system_instruction, response_schema, etc.) must be forwarded
    to both probe calls (peer-review batch, July 2026): the original
    version dropped them, so a caller requiring structured JSON output got
    two unformatted, wildly divergent probe responses -- guaranteed
    maximum Jaccard distance regardless of the model's actual
    uncertainty, forcing tiered_call to escalate on every single call and
    silently defeating the whole cost-tiering mechanism. `temperature` is
    intentionally excluded and fixed at 0.7 for both probes regardless of
    what the caller passed -- a deterministic (temperature=0) probe would
    make the two samples trivially identical, making the self-consistency
    signal meaningless.
    """
    from self_governance.graph_memory import tokenize as _tokenize, jaccard as _jaccard

    probe_kwargs = {k: v for k, v in kwargs.items() if k != "temperature"}
    first = provider.generate_content(prompt, api_key, model=draft_model, temperature=0.7, **probe_kwargs)
    second = provider.generate_content(prompt, api_key, model=draft_model, temperature=0.7, **probe_kwargs)
    t1, t2 = _tokenize(first.get("text", "")), _tokenize(second.get("text", ""))
    if not t1 and not t2:
        return 1.0  # both empty/errored -- treat as maximally uncertain, escalate
    agreement = _jaccard(t1, t2)
    return 1.0 - agreement


def tiered_call(
    provider: LLMProvider,
    prompt: str,
    api_key: Optional[str] = None,
    strong_model: Optional[str] = None,
    draft_model: Optional[str] = None,
    uncertainty_threshold: float = 0.5,
    **kwargs: Any,
) -> Dict[str, Any]:
    """Cost-tiered model escalation: tries a cheap draft model first, and
    only escalates to the full-price strong_model when the draft's output
    looks uncertain (see _self_consistency_uncertainty). Beat cascading and
    routing baselines on 25/27 setups in the source paper, using no
    separate router model -- just the draft model's own uncertainty.

    Not wired into any persona-dispatch path; a caller opts a specific LLM
    call into cost-tiering by calling this instead of provider.generate_content
    directly.

    Args:
        provider: an LLMProvider instance (GeminiProvider/OpenRouterProvider).
        prompt: the prompt to send.
        api_key: API key for both draft and strong calls.
        strong_model: escalation target; defaults to DEFAULT_MODEL.
        draft_model: cheap first-pass model; defaults to config.DRAFT_MODEL.
        uncertainty_threshold: escalate when the self-consistency
            uncertainty proxy exceeds this, in [0.0, 1.0].
        **kwargs: forwarded to generate_content for both calls.

    Returns:
        The dict from whichever call was used, plus a "tier" key
        ("draft" or "escalated") so callers can see which model answered.
    """
    draft_model = draft_model or DRAFT_MODEL
    strong_model = strong_model or DEFAULT_MODEL

    uncertainty = _self_consistency_uncertainty(provider, prompt, api_key, draft_model, **kwargs)
    if uncertainty <= uncertainty_threshold:
        result = provider.generate_content(prompt, api_key, model=draft_model, **kwargs)
        result["tier"] = "draft"
        return result

    result = provider.generate_content(prompt, api_key, model=strong_model, **kwargs)
    result["tier"] = "escalated"
    return result


def get_provider(api_key: Optional[str] = None, model: Optional[str] = None) -> LLMProvider:
    """Dispatches on the API key's own prefix, so no separate --provider
    flag is needed: an OpenRouter key (sk-or-...) routes through
    OpenRouterProvider (Claude, GPT, Llama, or any other OpenRouter-hosted
    model); anything else routes through GeminiProvider."""
    if api_key and api_key.startswith("sk-or-"):
        return OpenRouterProvider()
    return GeminiProvider()
