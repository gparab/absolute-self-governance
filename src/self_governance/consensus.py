import os
import random
import math
from typing import List, Optional, Any
from self_governance.tracing import tracer


class ConsensusResult(tuple):
    """
    Result of a succession consensus run.
    Contains the approved roster, final temperature, and final threshold.
    """
    def __new__(cls, approved_roster: List[str], final_temperature: float, final_threshold: float) -> "ConsensusResult":
        obj = super().__new__(cls, (approved_roster, final_temperature, final_threshold))
        obj.prompt_tokens = 0
        obj.completion_tokens = 0
        return obj

    @property
    def approved_roster(self) -> List[str]:
        """The list of agents approved in the consensus session."""
        return self[0]

    @property
    def final_temperature(self) -> float:
        """The final simulation temperature."""
        return self[1]

    @property
    def final_threshold(self) -> float:
        """The final threshold approval rate required for roster inclusion."""
        return self[2]


def run_consensus(
    initial_roster: List[str],
    B: int = 3,
    target_tau: float = 9.0,
    initial_temp: float = 1.0,
    gamma: float = 0.1,
    delta: float = 0.5,
    seed: Optional[int] = None,
    adapter: Optional[Any] = None,
    requirements: Optional[List[float]] = None
) -> ConsensusResult:
    """
    Run an iterative simulation of voting consensus (TETD consensus).

    If a threshold approval is not reached within B iterations, it increases the
    simulation temperature by gamma and decays the approval threshold by delta per iteration
    (with a minimum cap of 7.0 or 70% approval rate).

    Args:
        initial_roster: List of candidate agent IDs.
        B: Number of iterations before temperature scaling begins. Must be positive.
        target_tau: Initial target approval threshold. Must be a finite number.
        initial_temp: Initial simulation temperature. Must be non-negative.
        gamma: Temperature increment per iteration. Must be non-negative.
        delta: Threshold decay rate per iteration. Must be positive (greater than 0.0).

    Returns:
        A ConsensusResult containing:
            - approved_roster: The roster of agents meeting the consensus threshold.
            - final_temperature: The simulation temperature at the end of the run.
            - final_threshold: The threshold tau at the end of the run.

    Raises:
        ValueError: If validation of any parameter fails (e.g. B <= 0, delta <= 0.0,
                    or target_tau is not finite).
    """
    # 1. Validate inputs
    if not isinstance(initial_roster, list):
        raise TypeError("initial_roster must be a list")
    if not all(isinstance(agent, str) for agent in initial_roster):
        raise TypeError("all elements in initial_roster must be strings")
    if not isinstance(B, int) or B <= 0:
        raise ValueError("B must be a positive integer")
    if not isinstance(initial_temp, (int, float)) or not math.isfinite(initial_temp) or initial_temp < 0.0:
        raise ValueError("initial_temp must be non-negative")
    if not isinstance(gamma, (int, float)) or not math.isfinite(gamma) or gamma < 0.0:
        raise ValueError("gamma must be non-negative")
    if not isinstance(delta, (int, float)) or not math.isfinite(delta) or delta <= 0.0:
        raise ValueError("delta must be greater than 0.0")
    if not isinstance(target_tau, (int, float)) or not math.isfinite(target_tau):
        raise ValueError("target_tau must be a finite number")

    with tracer.start_as_current_span("run_consensus") as span:
        span.set_attribute("initial_roster", ",".join(initial_roster))
        # Deduplicate initial_roster preserving order at the beginning of run_consensus
        unique_roster = []
        for agent in initial_roster:
            if agent not in unique_roster:
                unique_roster.append(agent)
        initial_roster = unique_roster

        if not initial_roster:
            return ConsensusResult([], float(initial_temp), float(target_tau))

        if seed is not None:
            rng = random.Random(seed)
        else:
            rng = random.Random()
        temp = float(initial_temp)
        tau = float(target_tau)
        iteration = 1

        api_key = os.getenv("GEMINI_API_KEY")

        if api_key and adapter is None:
            from self_governance.gemini_adapter import GeminiExecutionAdapter
            adapter = GeminiExecutionAdapter(api_key=api_key)

        from self_governance.metrics import ASG_CONSENSUS_ITERATIONS

        justifications = {}

        while True:
            ASG_CONSENSUS_ITERATIONS.inc()
            scores = {}
            new_justifications = {}
            for agent in initial_roster:
                peer_feedback = ""
                if justifications:
                    peer_feedback = "Here is the peer feedback from the previous round of deliberation:\n" + "\n".join(
                        f"- '{a}' was rated {info['score']}. Peer justification: {info['justification']}"
                        for a, info in justifications.items()
                    ) + "\n\n"

                from self_governance.agency_agents_adapter import get_persona, get_capability_prompt
                persona = get_persona(agent)
                
                capability_info = ""
                if requirements:
                    resolved_caps = []
                    if len(requirements) > 0 and requirements[0] > 0.0:
                        resolved_caps.append("sqlite_concurrency")
                    if len(requirements) > 1 and requirements[1] > 0.0:
                        resolved_caps.extend(["hmac_verification", "path_traversal_hardening"])
                    if len(requirements) > 2 and requirements[2] > 0.0:
                        resolved_caps.append("pytest_coverage")
                        
                    if resolved_caps:
                        capability_info = "Associated Capabilities/Skills Guidelines:\n"
                        for cap in resolved_caps:
                            prompt_chunk = get_capability_prompt(cap)
                            if prompt_chunk:
                                capability_info += f"- {prompt_chunk}\n"
                
                persona_info = f"Agent Persona Guidelines: {persona['prompt']}\nDivision: {persona['division']}\nDescription: {persona['description']}\n{capability_info}"

                if api_key and adapter is not None:
                    prompt = (
                        f"{peer_feedback}"
                        f"You are evaluating the agent role '{agent}' for software engineering tasks.\n"
                        f"{persona_info}"
                        f"The full list of candidate agent roles under consideration is: {initial_roster}.\n"
                        "Considering the feedback from your peers (if any), rate the suitability of this agent compared to the others.\n"
                        "Format your response exactly as:\n"
                        "Score: <number between 1.0 and 10.0>\n"
                        "Reason: <brief justification of why this role is suitable or not>"
                    )
                    res = adapter._call_gemini_and_track(prompt)
                    score = 7.5
                    justification = "No justification provided."
                    if "Score:" in res:
                        try:
                            parts = res.split("Reason:")
                            score_part = parts[0].replace("Score:", "").strip()
                            score = float(score_part)
                            if len(parts) > 1:
                                justification = parts[1].strip()
                        except Exception:
                            score = 7.5
                    else:
                        try:
                            score = float(res)
                        except Exception:
                            score = 7.5
                else:
                    if iteration <= B:
                        score = 8.0 + rng.uniform(-0.1, 0.1)
                    else:
                        escape_term = abs(rng.uniform(-0.01, 0.01) * temp)
                        score = 7.0 + rng.uniform(0.01, 0.09) + min(0.1, escape_term)
                    justification = f"Mock justification for {agent} at iteration {iteration}"

                scores[agent] = score
                new_justifications[agent] = {"score": score, "justification": justification}
            
            justifications = new_justifications
            
            avg_score = sum(scores.values()) / len(initial_roster)

            if avg_score >= tau:
                approved = [agent for agent, score in scores.items() if score >= tau]
                result = ConsensusResult(approved, temp, tau)
                if adapter is not None:
                    result.prompt_tokens = adapter.prompt_tokens
                    result.completion_tokens = adapter.completion_tokens
                return result

            # Add a safety loop iteration limit of 1000. If iteration > 1000,
            # break the loop and return the best effort ConsensusResult.
            if iteration > 1000:
                approved = [agent for agent, score in scores.items() if score >= tau]
                if not approved:
                    max_agent = max(scores, key=scores.get)
                    approved = [max_agent]
                result = ConsensusResult(approved, temp, tau)
                if adapter is not None:
                    result.prompt_tokens = adapter.prompt_tokens
                    result.completion_tokens = adapter.completion_tokens
                return result

            # Update temp and tau for the next iteration if iteration threshold is met
            if iteration >= B:
                temp += gamma
                tau = max(7.0, tau - delta)

            iteration += 1
