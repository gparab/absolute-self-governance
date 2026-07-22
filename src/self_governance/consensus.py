"""Consensus mechanisms module for Absolute Self-Governance.

Provides both TETD (Thermal Escape & Threshold Decay) consensus engine for
succession planning, and a PBFT consensus engine with Raft-style log
consistency checking.
"""

import os
import random
import math
import json
import itertools
import time as _time
import logging
from dataclasses import dataclass
from typing import List, Optional, Any

from self_governance.tracing import tracer
from self_governance.agency_agents_adapter import get_persona, get_capability_prompt
from self_governance.gemini_adapter import GeminiExecutionAdapter
from self_governance.metrics import ASG_CONSENSUS_ITERATIONS
from self_governance.config import OrchestratorConfig
from self_governance.graph_memory import tokenize as _tokenize, jaccard as _jaccard

logger = logging.getLogger("self_governance.consensus")

# Justifications are stored in plaintext. The previous XOR/base64 "encryption"
# was obfuscation with a hardcoded key — false security worse than none.

_GROUPTHINK_JACCARD_THRESHOLD = 0.6


def _detect_groupthink(justifications: dict, approved_agents: List[str]) -> bool:
    """Groupthink-suspicion heuristic (Janis 1972 taxonomy, July 2026
    topic-page batch): unanimous approval alone doesn't distinguish genuine
    independent agreement from agents converging on the same shallow
    reasoning. Flags the round when every approved agent's justification is
    lexically near-identical to every other's (mean pairwise Jaccard above
    threshold) AND approval was unanimous -- informational only, doesn't
    change the vote outcome.
    """
    if len(approved_agents) < 2:
        return False
    texts = [str(justifications[a]["justification"]) for a in approved_agents]
    token_sets = [_tokenize(t) for t in texts]
    if any(not ts for ts in token_sets):
        return False
    pairs = list(itertools.combinations(range(len(token_sets)), 2))
    similarities = [_jaccard(token_sets[i], token_sets[j]) for i, j in pairs]
    return (sum(similarities) / len(similarities)) >= _GROUPTHINK_JACCARD_THRESHOLD


def _weighted_average(scores: dict, weights: Optional[dict] = None) -> float:
    """Collective-intelligence-factor weighting (Woolley et al. 2010, July
    2026 topic-page batch): a flat mean when weights is None/empty (weight
    1.0 for every agent), or a weighted mean when the caller supplies
    per-agent weights (e.g. derived from historical calibration) -- an
    agent missing from weights still defaults to 1.0.
    """
    weights = weights or {}
    total_weight = sum(weights.get(a, 1.0) for a in scores)
    return sum(scores[a] * weights.get(a, 1.0) for a in scores) / total_weight


_SEQUENTIAL_MARKERS = (
    "then", "after", "once", "depends on", "followed by", "before",
    "first,", "next,", "finally,", "step 1", "step 2",
)


def aggregate_aspect_scores(aspect_scores: dict, weights: Optional[dict] = None) -> float:
    """Aspect-decomposed verification (BoN-MAV, research.google survey, July
    2026 topic-page batch): instead of one holistic peer vote per candidate,
    BoN-MAV scores separate aspects (e.g. "correctness", "safety", "style")
    with dedicated verifiers and combines them, outperforming single-
    verifier/self-consistency scoring at the same test-time compute budget.

    This is a thin wrapper around the existing _weighted_average -- reuses
    TETD's calibration-weighting machinery, just applied across aspects of
    one candidate instead of across agents voting on one candidate. Not
    wired into ConsensusEngine.run()'s control flow; a caller assembles
    per-aspect scores (e.g. from separate LLM calls, one per aspect) and
    passes them here to get one aggregate score to feed into the existing
    vote.

    Args:
        aspect_scores: mapping of aspect name -> score in [0.0, 1.0].
        weights: optional per-aspect weight (e.g. "correctness" weighted
            higher than "style"); aspects missing from weights default to 1.0.

    Returns:
        The weighted-mean aspect score in [0.0, 1.0].
    """
    if not aspect_scores:
        raise ValueError("aspect_scores must be non-empty")
    return _weighted_average(aspect_scores, weights)


def estimate_task_decomposability(
    description: str, single_agent_success_estimate: Optional[float] = None
) -> float:
    """Heuristic estimate of whether a task reads as parallelizable
    (independent sub-goals a multi-agent vote can usefully deliberate
    over) or sequential (one continuous chain of steps where each depends
    on the last).

    Google Research's agent-scaling study (research.google survey, July
    2026 topic-page batch: "Towards a science of scaling agent systems")
    found multi-agent coordination helps parallelizable tasks by up to
    +80.9% but *hurts* sequential-reasoning tasks by 39-70%, using task
    decomposability (fit from real data, R^2=0.513) as the deciding
    signal for which architecture to use ahead of time.

    This is a plain keyword heuristic, not a trained classifier -- ASG
    doesn't do model training, and a fitted classifier needs labeled
    outcome data ASG doesn't have yet. It's deliberately NOT wired into
    ConsensusEngine's control flow here: a caller (e.g. the webhook
    dispatch path) can use the score to decide whether multi-round TETD
    deliberation is worth invoking for a given task, but changing that
    dispatch behavior is a separate decision from providing the signal.

    The same survey's headline model ("Towards a science of scaling agent
    systems") adds a second axis this function didn't originally have:
    multi-agent gains diminish once single-agent performance on a task
    type already clears a threshold, independent of how decomposable the
    task reads. single_agent_success_estimate lets a caller who has a
    cached/estimated solo-baseline success rate for this task type (e.g.
    from GraphMemoryEngine's EMA-scored history) fold that in: a high solo
    estimate dampens the returned score toward neutral (0.5), since
    swarming a task the solo agent already handles well isn't worth it
    regardless of its structure.

    Args:
        description: Free-text task/issue description.
        single_agent_success_estimate: Optional solo-baseline success rate
            in [0.0, 1.0] for this task type, if known. None (default)
            skips the dampening and returns the structure-only score.

    Returns:
        A float in [0.0, 1.0]: higher means more parallelizable
        (independent, conjunctive sub-goals -- "and", bullet lists,
        multiple distinct nouns), lower means more sequential (temporal/
        dependency connectives like "then", "after", "depends on").
    """
    text = description.lower()
    if not text.strip():
        base_score = 0.5
    else:
        sequential_hits = sum(text.count(marker) for marker in _SEQUENTIAL_MARKERS)
        conjunctive_hits = text.count(" and ") + text.count("\n-") + text.count("\n*")

        total_signal = sequential_hits + conjunctive_hits
        base_score = 0.5 if total_signal == 0 else conjunctive_hits / total_signal

    if single_agent_success_estimate is None:
        return base_score

    solo = max(0.0, min(1.0, single_agent_success_estimate))
    return base_score * (1.0 - solo) + 0.5 * solo


@dataclass(frozen=True)
class ConsensusResult:
    """Result of a succession consensus run.

    Attributes:
        approved_roster: List of agent role names that reached consensus.
        final_temperature: The temperature parameter value when consensus finished.
        final_threshold: The consensus score threshold value when consensus finished.
        prompt_tokens: Cumulative number of prompt tokens used in consensus.
        completion_tokens: Cumulative number of completion tokens generated in consensus.
        groupthink_suspected: True if the winning round looked like suspiciously
            uniform agreement (see _detect_groupthink) rather than independent
            judgment converging -- an informational flag, not a rejection.
    """
    approved_roster: List[str]
    final_temperature: float
    final_threshold: float
    prompt_tokens: int = 0
    completion_tokens: int = 0
    groupthink_suspected: bool = False

    def __iter__(self):
        return iter((self.approved_roster, self.final_temperature, self.final_threshold))

    def __getitem__(self, index):
        return (self.approved_roster, self.final_temperature, self.final_threshold)[index]

    def __len__(self):
        return 3


class ConsensusEngine:
    """Consensus engine to coordinate the succession consensus run (TETD consensus).

    Implements thermal escape and threshold decay (TETD) to prevent voting deadlocks.
    """

    def __init__(
        self,
        initial_roster: List[str],
        B: int = 3,
        target_tau: float = 9.0,
        initial_temp: float = 1.0,
        gamma: float = 0.1,
        delta: float = 0.5,
        seed: Optional[int] = None,
        adapter: Optional[Any] = None,
        requirements: Optional[List[float]] = None,
        T_max: float = 2.0,
        model: Optional[str] = None,
        max_seconds: float = 600.0,
        config_path: Optional[str] = None,
        anti_sycophancy: bool = False,
        calibration_weights: Optional[dict] = None,
        enable_debate: bool = False,
        debate_margin: float = 1.0,
    ):
        """Initializes the ConsensusEngine.

        Args:
            initial_roster: The initial candidate agent roles list.
            B: Buffer iterations limit before temperature and decay kick in.
            target_tau: Initial target score threshold (1.0 to 10.0).
            initial_temp: Starting temperature value for simulated annealing.
            gamma: Temperature increase step size.
            delta: Threshold decay step size.
            seed: Optional random number generator seed.
            adapter: Optional execution adapter to call LLM services.
            requirements: Optional task requirement floats mapping to capabilities.
            T_max: Maximum allowed temperature value.
            model: Optional LLM name to override.
            max_seconds: Max seconds to run before deadline termination.
            config_path: Optional path to YAML configuration.
            anti_sycophancy: If True, peer feedback shown to each voter omits
                other agents' numeric scores (justification text only), to
                reduce anchoring toward the visible prior-round consensus
                (Sharma et al. 2023 sycophancy research, July 2026 topic-page
                batch). Off by default -- identical to prior behavior.
            calibration_weights: Optional per-agent weight (default 1.0 for
                any agent not listed) applied to that agent's score when
                computing the round's average (Woolley et al. 2010 collective-
                intelligence-factor research, July 2026 topic-page batch):
                a more historically-calibrated voter's score counts for more
                than an uncalibrated one's, instead of a flat mean. Callers
                are responsible for deriving weights (e.g. from procedural-
                memory track record); omitting this preserves the flat mean.
            enable_debate: If True, a round whose average score lands within
                debate_margin of tau triggers one extra debate round before
                the pass/fail decision: every agent is shown the single
                strongest dissenting justification and asked to directly
                address it, and that round's scores replace the contested
                round's (Du et al. 2023 multiagent-debate research, July
                2026 topic-page batch). Off by default.
            debate_margin: How close a round's average score must be to tau
                to count as "contested" and trigger the debate round above.

        Raises:
            TypeError: If initial_roster is not a list of strings, or B, initial_temp,
                gamma, delta, or target_tau fail validation.
            ValueError: If roster exceeds 100 agents under LLM execution, or parameter
                bounds are violated.
        """
        # 1. Validate inputs
        if not isinstance(initial_roster, list):
            raise TypeError("initial_roster must be a list")
        if not all(isinstance(agent, str) for agent in initial_roster):
            raise TypeError("all elements in initial_roster must be strings")
        if adapter is not None and len(initial_roster) > 100:
            raise ValueError(
                "initial_roster exceeds the maximum size of 100 agents for LLM-backed consensus"
            )
        if not isinstance(B, int) or B <= 0:
            raise ValueError("B must be a positive integer")
        if (
            not isinstance(initial_temp, (int, float))
            or not math.isfinite(initial_temp)
            or initial_temp < 0.0
        ):
            raise ValueError("initial_temp must be non-negative")
        if not isinstance(gamma, (int, float)) or not math.isfinite(gamma) or gamma < 0.0:
            raise ValueError("gamma must be non-negative")
        if not isinstance(delta, (int, float)) or not math.isfinite(delta) or delta <= 0.0:
            raise ValueError("delta must be greater than 0.0")
        if not isinstance(target_tau, (int, float)) or not math.isfinite(target_tau):
            raise ValueError("target_tau must be a finite number")

        self.initial_roster = list(dict.fromkeys(initial_roster))
        self.B = B
        self.target_tau = target_tau
        self.initial_temp = initial_temp
        self.gamma = gamma
        self.delta = delta
        self.seed = seed
        self.requirements = requirements
        self.T_max = T_max
        self.model = model
        self.max_seconds = max_seconds
        self.anti_sycophancy = anti_sycophancy
        self.calibration_weights = calibration_weights or {}
        self.enable_debate = enable_debate
        self.debate_margin = debate_margin

        # Annealing jitter, not cryptography: reproducibility via seed matters,
        # unpredictability does not.
        if self.seed is not None:
            self.rng = random.Random(self.seed)  # nosec B311
        else:
            self.rng = random.Random()  # nosec B311

        self.temp = float(self.initial_temp)
        self.tau = float(self.target_tau)
        self.iteration = 1

        self.api_key = os.getenv("GEMINI_API_KEY")
        self.adapter: Optional[Any] = None
        if self.api_key and adapter is None:
            self.adapter = GeminiExecutionAdapter(api_key=self.api_key, config_path=config_path)
        else:
            self.adapter = adapter

        self.config: Optional[OrchestratorConfig] = None
        try:
            self.config = OrchestratorConfig(config_path)
            self.advisor_enabled = self.config.advisor_enabled
            self.nudge_turn = self.config.advisor_nudge_turn
            self.nudge_text = self.config.advisor_nudge_text
        except Exception:
            logger.warning("Failed to initialize OrchestratorConfig in ConsensusEngine; falling back to default advisor configurations.", exc_info=True)
            self.advisor_enabled = True
            self.nudge_turn = 2
            self.nudge_text = "Please call advisor() before committing to an approach or declaring completion."
            self.config = None

        self.justifications: dict[str, dict[str, Any]] = {}
        self.scores: dict[str, float] = {}
        self.advisor_called = False

    def _invoke_advisor(self, peer_feedback: str) -> str:
        """Consults the advisor and appends instructions to peer feedback.

        Args:
            peer_feedback: The consolidated feedback string from previous rounds.

        Returns:
            The feedback string, potentially updated with advisor's strategic advice.
        """
        if (
            self.advisor_enabled
            and self.iteration == self.nudge_turn
            and not self.advisor_called
            and self.adapter is not None
        ):
            convo = [
                {
                    "role": "user",
                    "content": (
                        f"Consensus Turn Nudge at Iteration {self.iteration}.\n"
                        f"Goal: Achieve succession consensus on roster {self.initial_roster}.\n"
                        f"Current threshold tau is {self.tau:.2f}, temperature is {self.temp:.2f}.\n"
                        f"Nudge instruction: {self.nudge_text}\n"
                        f"Voter justifications so far: {self.justifications}"
                    )
                }
            ]
            advisor_res = self.adapter.consult_advisor(convo)
            self.advisor_called = True
            advisor_advice = advisor_res.get("output", "")
            if advisor_advice:
                peer_feedback += f"Advisor Strategic Advice: {advisor_advice}\n\n"
        return peer_feedback

    # Fail-closed default (looper's judge-verdict parsing rule, July 2026
    # topic-page batch): a totally unparseable vote must count as a dissent,
    # never a silent pass. The prior default of 7.5 sat just above the
    # consensus threshold's 7.0 floor -- an uninterpretable response could
    # have counted as approval in a decayed round. 0.0 is unambiguous.
    _PARSE_FAILURE_SCORE = 0.0
    _PARSE_FAILURE_JUSTIFICATION = "PARSE_FAILURE: raw response could not be interpreted as a valid vote; treated as dissent (fail-closed)."

    def _parse_llm_score(self, res: str) -> tuple[float, str]:
        """Parses the score and justification from the LLM's raw response.

        Args:
            res: Raw text response from the LLM.

        Returns:
            A tuple of (score (float), justification (str)). On total parse
            failure, returns (_PARSE_FAILURE_SCORE, _PARSE_FAILURE_JUSTIFICATION)
            -- fail-closed, never a silent moderate-to-good default.
        """
        score = self._PARSE_FAILURE_SCORE
        justification = self._PARSE_FAILURE_JUSTIFICATION
        try:
            data = json.loads(res)
            score = float(data.get("score", self._PARSE_FAILURE_SCORE))
            justification = data.get("reason", "No justification provided.")
        except Exception:
            logger.warning("Failed to parse LLM response as JSON. Falling back to regex/text heuristics. Raw response: %r", res, exc_info=True)
            if "Score:" in res:
                try:
                    parts = res.split("Reason:")
                    score_part = parts[0].replace("Score:", "").strip()
                    score = float(score_part)
                    if len(parts) > 1:
                        justification = parts[1].strip()
                except Exception:
                    logger.warning("Failed to parse Score/Reason text formatting. Raw response: %r", res, exc_info=True)
                    score = self._PARSE_FAILURE_SCORE
                    justification = self._PARSE_FAILURE_JUSTIFICATION
            else:
                try:
                    score = float(res)
                    justification = "No justification provided."
                except Exception:
                    logger.warning("Failed to parse response as float. Treating as dissent (fail-closed). Raw response: %r", res, exc_info=True)
                    score = self._PARSE_FAILURE_SCORE
                    justification = self._PARSE_FAILURE_JUSTIFICATION
        return score, justification

    def _score_agent(self, agent: str, peer_feedback: str) -> tuple[float, str]:
        """Computes the score and justification for a single agent role.

        Args:
            agent: The candidate role name to evaluate.
            peer_feedback: Aggregated dialogue context from other voters.

        Returns:
            A tuple of (score (float), justification (str)).
        """
        registry_path = None
        if hasattr(self, "config") and self.config is not None:
            registry_path = self.config.project_persona_registry
        persona = get_persona(agent, registry_path=registry_path)

        capability_info = ""
        if self.requirements:
            resolved_caps = []
            if len(self.requirements) > 0 and self.requirements[0] > 0.0:
                resolved_caps.append("sqlite_concurrency")
            if len(self.requirements) > 1 and self.requirements[1] > 0.0:
                resolved_caps.extend(["hmac_verification", "path_traversal_hardening"])
            if len(self.requirements) > 2 and self.requirements[2] > 0.0:
                resolved_caps.append("pytest_coverage")

            if resolved_caps:
                capability_info = "Associated Capabilities/Skills Guidelines:\n"
                for cap in resolved_caps:
                    prompt_chunk = get_capability_prompt(cap)
                    if prompt_chunk:
                        capability_info += f"- {prompt_chunk}\n"

        is_reasoning = self.adapter.is_reasoning_model(self.model) if self.adapter is not None else False
        persona_prompt = (
            persona.get("developer_message") or persona.get("prompt")
            if is_reasoning
            else persona.get("prompt")
        )

        persona_info = (
            f"Agent Persona Guidelines: {persona_prompt}\n"
            f"Division: {persona['division']}\n"
            f"Description: {persona['description']}\n"
            f"{capability_info}"
        )

        if self.api_key and self.adapter is not None:
            prompt = (
                f"{peer_feedback}"
                f"You are evaluating the agent role '{agent}' for software engineering tasks.\n"
                f"{persona_info}"
                f"The full list of candidate agent roles under consideration is: {self.initial_roster}.\n"
                "Considering the feedback from your peers (if any), rate the suitability of this agent compared to the others.\n"
                "Return a JSON object containing a float score and justification reason."
            )
            schema = {
                "type": "OBJECT",
                "properties": {
                    "score": {
                        "type": "NUMBER",
                        "description": "Suitability score between 1.0 and 10.0.",
                    },
                    "reason": {
                        "type": "STRING",
                        "description": "Brief justification of why this role is suitable or not.",
                    },
                },
                "required": ["score", "reason"],
            }
            res = self.adapter._call_gemini_and_track(
                prompt,
                response_schema=schema,
                response_mime_type="application/json",
                model=self.model,
                temperature=self.temp,
            )
            score = 1.0
            justification = "API call failed; scored as rejection."
            if res:
                score, justification = self._parse_llm_score(res)
        else:
            base_score = 7.5
            if self.requirements:
                if agent == "Backend Wizard" and len(self.requirements) > 0:
                    base_score += min(2.0, self.requirements[0] * 0.5)
                elif agent == "Security Auditor" and len(self.requirements) > 1:
                    base_score += min(2.0, self.requirements[1] * 0.5)
                elif agent == "QA Specialist" and len(self.requirements) > 2:
                    base_score += min(2.0, self.requirements[2] * 0.5)

            if self.iteration <= self.B:
                score = base_score + 0.5 + self.rng.uniform(-0.1, 0.1)
            else:
                escape_term = abs(self.rng.uniform(-0.01, 0.01) * self.temp)
                score = base_score - 0.5 + self.rng.uniform(0.01, 0.09) + min(0.1, escape_term)
            justification = f"Suitability assessment for candidate {agent} based on capability alignment at iteration {self.iteration}."

        if not (1.0 <= score <= 10.0):
            score = 1.0

        return score, justification

    def run(self) -> ConsensusResult:
        """Run the TETD consensus simulation loop.

        Returns:
            ConsensusResult: The results containing approved roster, final temp, and threshold.
        """
        with tracer.start_as_current_span("run_consensus") as span:
            span.set_attribute("initial_roster", ",".join(self.initial_roster))

            if not self.initial_roster:
                return ConsensusResult(
                    approved_roster=[],
                    final_temperature=float(self.initial_temp),
                    final_threshold=float(self.target_tau),
                    prompt_tokens=self.adapter.prompt_tokens if self.adapter is not None else 0,
                    completion_tokens=self.adapter.completion_tokens if self.adapter is not None else 0,
                )

            deadline = _time.monotonic() + self.max_seconds

            while True:
                if _time.monotonic() > deadline and self.iteration > 1:
                    approved = [a for a, s in self.scores.items() if s >= self.tau]
                    if not approved and self.scores:
                        approved = [max(self.scores, key=lambda a: self.scores[a])]
                    logger.info("Consensus deadline exceeded at iteration %d. Terminating consensus run.", self.iteration)
                    return ConsensusResult(
                        approved_roster=approved,
                        final_temperature=self.temp,
                        final_threshold=self.tau,
                        prompt_tokens=self.adapter.prompt_tokens if self.adapter is not None else 0,
                        completion_tokens=self.adapter.completion_tokens if self.adapter is not None else 0,
                    )

                logger.info("Starting consensus iteration %d. Current temperature: %.2f, threshold: %.2f", self.iteration, self.temp, self.tau)

                ASG_CONSENSUS_ITERATIONS.inc()
                self.scores = {}
                new_justifications = {}
                peer_feedback = ""
                if self.justifications:
                    if self.anti_sycophancy:
                        # Anti-sycophancy (Sharma et al. 2023, July 2026 topic-page
                        # batch): omit the visible numeric score so a voter can't
                        # anchor toward the prior round's consensus number, while
                        # still seeing peers' reasoning.
                        peer_feedback = (
                            "Here is the peer feedback from the previous round of deliberation "
                            "(reasoning only -- form your own independent score):\n"
                            + "\n".join(
                                f"- '{a}' peer justification: {info['justification']}"
                                for a, info in self.justifications.items()
                            )
                            + "\n\n"
                        )
                    else:
                        peer_feedback = (
                            "Here is the peer feedback from the previous round of deliberation:\n"
                            + "\n".join(
                                f"- '{a}' was rated {info['score']}. Peer justification: {info['justification']}"
                                for a, info in self.justifications.items()
                            )
                            + "\n\n"
                        )

                peer_feedback = self._invoke_advisor(peer_feedback)

                for agent in self.initial_roster:
                    score, justification = self._score_agent(agent, peer_feedback)
                    logger.info("Agent %s evaluated score: %.2f", agent, score)
                    self.scores[agent] = score
                    new_justifications[agent] = {
                        "score": score,
                        "justification": justification,
                    }

                self.justifications = new_justifications

                # --- Quality gate filtering ---
                # Abstentions do not count toward the average.
                gated_scores: dict[str, float] = {}
                for agent, score in self.scores.items():
                    justification = str(new_justifications[agent]["justification"])
                    registry_path = None
                    if hasattr(self, "config") and self.config is not None:
                        registry_path = self.config.project_persona_registry
                    persona = get_persona(agent, registry_path=registry_path)
                    gate_data = persona.get("quality_gate")
                    if gate_data is not None:
                        from self_governance.models import PersonaQualityGate
                        gate = PersonaQualityGate(**gate_data)
                        if not gate.passes(score, justification):
                            logger.info(
                                "Agent %s vote suppressed by quality gate "
                                "(score=%.2f, confidence_floor=%.2f).",
                                agent, score, gate.min_confidence,
                            )
                            continue  # abstain
                    gated_scores[agent] = score

                # Fall back to all scores if every vote was gated out
                effective_scores = gated_scores if gated_scores else self.scores
                avg_score = _weighted_average(effective_scores, self.calibration_weights)
                logger.info("Consensus iteration %d average score: %.2f", self.iteration, avg_score)

                # Debate phase for contested votes (Du et al. 2023 multiagent-
                # debate research, July 2026 topic-page batch): a round that
                # lands close to tau gets one extra round where every agent
                # must directly address the strongest dissenting justification,
                # instead of finalizing on the first pass's numbers.
                if (
                    self.enable_debate
                    and len(effective_scores) > 1
                    and abs(avg_score - self.tau) <= self.debate_margin
                ):
                    dissenter = min(effective_scores, key=lambda a: effective_scores[a])
                    debate_feedback = (
                        "Debate round (contested vote): the strongest objection came "
                        f"from '{dissenter}': \"{new_justifications[dissenter]['justification']}\"\n"
                        "Directly address this specific objection in your revised assessment.\n\n"
                    )
                    debate_scores: dict[str, float] = {}
                    debate_justifications: dict = {}
                    for agent in self.initial_roster:
                        score, justification = self._score_agent(agent, debate_feedback)
                        debate_scores[agent] = score
                        debate_justifications[agent] = {"score": score, "justification": justification}
                    self.scores = debate_scores
                    self.justifications = debate_justifications
                    effective_scores = debate_scores
                    avg_score = _weighted_average(effective_scores, self.calibration_weights)
                    logger.info("Debate round revised average score to %.2f", avg_score)

                if avg_score >= self.tau:
                    approved = [agent for agent, score in effective_scores.items() if score >= self.tau]
                    logger.info("Consensus successfully achieved at iteration %d (Average Score: %.2f >= Threshold: %.2f). Roster: %s", self.iteration, avg_score, self.tau, approved)
                    return ConsensusResult(
                        approved_roster=approved,
                        final_temperature=self.temp,
                        final_threshold=self.tau,
                        prompt_tokens=self.adapter.prompt_tokens if self.adapter is not None else 0,
                        completion_tokens=self.adapter.completion_tokens if self.adapter is not None else 0,
                        groupthink_suspected=_detect_groupthink(self.justifications, approved),
                    )

                if self.iteration > 1000:
                    approved = [agent for agent, score in effective_scores.items() if score >= self.tau]
                    if not approved:
                        approved = [max(effective_scores, key=lambda a: effective_scores[a])]
                    logger.info("Consensus exceeded max iteration limit (1000 rounds). Terminating run.")
                    return ConsensusResult(
                        approved_roster=approved,
                        final_temperature=self.temp,
                        final_threshold=self.tau,
                        prompt_tokens=self.adapter.prompt_tokens if self.adapter is not None else 0,
                        completion_tokens=self.adapter.completion_tokens if self.adapter is not None else 0,
                    )

                if self.iteration >= self.B:
                    self.temp = min(self.T_max, self.temp + self.gamma)
                    self.tau = max(7.0, self.tau - self.delta)
                    logger.info("Decaying threshold and increasing temperature (New temperature: %.2f, new threshold: %.2f)", self.temp, self.tau)

                self.iteration += 1


def run_consensus(
    initial_roster: List[str],
    B: int = 3,
    target_tau: float = 9.0,
    initial_temp: float = 1.0,
    gamma: float = 0.1,
    delta: float = 0.5,
    seed: Optional[int] = None,
    adapter: Optional[Any] = None,
    requirements: Optional[List[float]] = None,
    T_max: float = 2.0,
    model: Optional[str] = None,
    max_seconds: float = 600.0,
    config_path: Optional[str] = None,
) -> ConsensusResult:
    """Run an iterative simulation of voting consensus (TETD consensus).

    Args:
        initial_roster: Roster candidates list.
        B: Initial buffer rounds before scaling.
        target_tau: Approval threshold target.
        initial_temp: Initial temperature value.
        gamma: Temperature increment per turn.
        delta: Threshold decay decrement per turn.
        seed: Random generator seed.
        adapter: Execution adapter instance.
        requirements: Requirements scores list.
        T_max: Maximum temperature cap.
        model: LLM model target override.
        max_seconds: Maximum allowed time.
        config_path: Path to configurations file.

    Returns:
        ConsensusResult: Roster consensus outcome.
    """
    engine = ConsensusEngine(
        initial_roster=initial_roster,
        B=B,
        target_tau=target_tau,
        initial_temp=initial_temp,
        gamma=gamma,
        delta=delta,
        seed=seed,
        adapter=adapter,
        requirements=requirements,
        T_max=T_max,
        model=model,
        max_seconds=max_seconds,
        config_path=config_path,
    )
    return engine.run()


class PBFTConsensusEngine:
    """PBFT Consensus Engine supporting PBFT state machine and Raft log consistency.

    Enforces agreement on execution logs using pre-prepare, prepare, and commit
    phases.
    """

    def __init__(self, node_id: str, peers: List[str], f: int, log: Optional[List[dict]] = None):
        """Initializes the PBFTConsensusEngine.

        Args:
            node_id: Unique string identifier of this consensus node.
            peers: List of peer node IDs in the network.
            f: The maximum number of tolerated faulty/byzantine nodes.
            log: Optional initial log list.
        """
        self.node_id = node_id
        self.peers = peers
        self.f = f
        self.current_term = 0
        self.log = log if log is not None else []
        self.commit_index = 0
        self.state = "Pre-prepared"
        self.prepare_votes: dict[tuple[int, int, str], set[str]] = {}
        self.commit_votes: dict[tuple[int, int, str], set[str]] = {}

    def verify_network_size(self) -> bool:
        """Verifies if network size meets the PBFT requirement N >= 3f + 1.

        Returns:
            True if the total nodes count is at least 3f + 1, False otherwise.
        """
        N = len(self.peers) + 1
        return N >= 3 * self.f + 1

    def receive_pre_prepare(self, leader_id: str, term: int, index: int, message: str) -> bool:
        """Handles a Pre-prepare message from the leader.

        Transition to "Prepared" state if term is valid.

        Args:
            leader_id: The ID of the sending leader node.
            term: The consensus term / view number.
            index: The log entry index.
            message: The proposed entry content or hash.

        Returns:
            True if the message is accepted, False otherwise.
        """
        if term < self.current_term:
            return False
        self.current_term = term
        self.state = "Prepared"
        return True

    def receive_prepare(self, sender_id: str, term: int, index: int, message: str) -> bool:
        """Handles a Prepare vote from a peer.

        Accumulates votes, transitioning to "Committed" once 2f votes are collected.

        Args:
            sender_id: The ID of the voting node.
            term: The consensus term / view number.
            index: The log entry index.
            message: The entry content or hash.

        Returns:
            True if 2f prepare votes threshold is reached, False otherwise.
        """
        if term < self.current_term:
            return False
        key = (term, index, message)
        if key not in self.prepare_votes:
            self.prepare_votes[key] = set()
        self.prepare_votes[key].add(sender_id)
        if len(self.prepare_votes[key]) >= 2 * self.f:
            self.state = "Committed"
            return True
        return False

    def receive_commit(self, sender_id: str, term: int, index: int, message: str) -> bool:
        """Handles a Commit vote from a peer.

        Accumulates commit votes. Confirms commit once 2f + 1 votes are received.

        Args:
            sender_id: The ID of the voting node.
            term: The consensus term / view number.
            index: The log entry index.
            message: The entry content or hash.

        Returns:
            True if 2f + 1 commit votes threshold is reached, False otherwise.
        """
        if term < self.current_term:
            return False
        key = (term, index, message)
        if key not in self.commit_votes:
            self.commit_votes[key] = set()
        self.commit_votes[key].add(sender_id)
        if len(self.commit_votes[key]) >= 2 * self.f + 1:
            return True
        return False

    def append_entries(
        self,
        term: int,
        leader_id: str,
        prev_log_index: int,
        prev_log_term: int,
        entries: List[dict],
        leader_commit: int,
    ) -> tuple[bool, int]:
        """Performs a Raft-style AppendEntries log consistency check.

        Validates previous log indices/terms, deletes conflicts, appends entries,
        and updates commit index.

        Args:
            term: The current term of the leader.
            leader_id: ID of the leader node.
            prev_log_index: The log index immediately preceding the new entries.
            prev_log_term: The term of the prev_log_index entry.
            entries: New log entries to store.
            leader_commit: The leader's commit index.

        Returns:
            A tuple of (success (bool), match_index (int)).
        """
        if term < self.current_term:
            return False, len(self.log) - 1

        self.current_term = term

        if prev_log_index >= 0:
            if prev_log_index >= len(self.log):
                return False, len(self.log) - 1
            if self.log[prev_log_index]["term"] != prev_log_term:
                return False, prev_log_index - 1

        # Delete conflicting entries and append new ones
        insert_idx = prev_log_index + 1
        for entry in entries:
            idx = entry.get("index", insert_idx)
            if idx < len(self.log):
                if self.log[idx]["term"] != entry["term"]:
                    self.log = self.log[:idx]
                    self.log.append(entry)
            else:
                self.log.append(entry)
            insert_idx = idx + 1

        if leader_commit > self.commit_index:
            self.commit_index = min(leader_commit, len(self.log) - 1)

        return True, len(self.log) - 1


