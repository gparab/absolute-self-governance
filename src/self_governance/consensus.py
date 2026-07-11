"""Consensus mechanisms module for Absolute Self-Governance.

Provides both TETD (Thermal Escape & Threshold Decay) consensus engine for
succession planning, and a PBFT consensus engine with Raft-style log
consistency checking.
"""

import os
import random
import math
import json
import time as _time
import logging
from dataclasses import dataclass
from typing import List, Optional, Any

from self_governance.tracing import tracer
from self_governance.agency_agents_adapter import get_persona, get_capability_prompt
from self_governance.gemini_adapter import GeminiExecutionAdapter
from self_governance.metrics import ASG_CONSENSUS_ITERATIONS
from self_governance.config import OrchestratorConfig

logger = logging.getLogger("self_governance.consensus")

# Justifications are stored in plaintext. The previous XOR/base64 "encryption"
# was obfuscation with a hardcoded key — false security worse than none.


@dataclass(frozen=True)
class ConsensusResult:
    """Result of a succession consensus run.

    Attributes:
        approved_roster: List of agent role names that reached consensus.
        final_temperature: The temperature parameter value when consensus finished.
        final_threshold: The consensus score threshold value when consensus finished.
        prompt_tokens: Cumulative number of prompt tokens used in consensus.
        completion_tokens: Cumulative number of completion tokens generated in consensus.
    """
    approved_roster: List[str]
    final_temperature: float
    final_threshold: float
    prompt_tokens: int = 0
    completion_tokens: int = 0

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

        if self.seed is not None:
            self.rng = random.Random(self.seed)
        else:
            self.rng = random.Random()

        self.temp = float(self.initial_temp)
        self.tau = float(self.target_tau)
        self.iteration = 1

        self.api_key = os.getenv("GEMINI_API_KEY")
        self.adapter: Optional[Any] = None
        if self.api_key and adapter is None:
            self.adapter = GeminiExecutionAdapter(api_key=self.api_key, config_path=config_path)
        else:
            self.adapter = adapter

        try:
            config = OrchestratorConfig(config_path)
            self.advisor_enabled = config.advisor_enabled
            self.nudge_turn = config.advisor_nudge_turn
            self.nudge_text = config.advisor_nudge_text
        except Exception:
            logger.warning("Failed to initialize OrchestratorConfig in ConsensusEngine; falling back to default advisor configurations.", exc_info=True)
            self.advisor_enabled = True
            self.nudge_turn = 2
            self.nudge_text = "Please call advisor() before committing to an approach or declaring completion."

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

    def _parse_llm_score(self, res: str) -> tuple[float, str]:
        """Parses the score and justification from the LLM's raw response.

        Args:
            res: Raw text response from the LLM.

        Returns:
            A tuple of (score (float), justification (str)).
        """
        score = 7.5
        justification = "No justification provided."
        try:
            data = json.loads(res)
            score = float(data.get("score", 7.5))
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
                    score = 7.5
            else:
                try:
                    score = float(res)
                except Exception:
                    logger.warning("Failed to parse response as float. Defaulting score to 7.5. Raw response: %r", res, exc_info=True)
                    score = 7.5
        return score, justification

    def _score_agent(self, agent: str, peer_feedback: str) -> tuple[float, str]:
        """Computes the score and justification for a single agent role.

        Args:
            agent: The candidate role name to evaluate.
            peer_feedback: Aggregated dialogue context from other voters.

        Returns:
            A tuple of (score (float), justification (str)).
        """
        persona = get_persona(agent)

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
                    persona = get_persona(agent)
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
                avg_score = sum(effective_scores.values()) / len(effective_scores)
                logger.info("Consensus iteration %d average score: %.2f", self.iteration, avg_score)

                if avg_score >= self.tau:
                    approved = [agent for agent, score in effective_scores.items() if score >= self.tau]
                    logger.info("Consensus successfully achieved at iteration %d (Average Score: %.2f >= Threshold: %.2f). Roster: %s", self.iteration, avg_score, self.tau, approved)
                    return ConsensusResult(
                        approved_roster=approved,
                        final_temperature=self.temp,
                        final_threshold=self.tau,
                        prompt_tokens=self.adapter.prompt_tokens if self.adapter is not None else 0,
                        completion_tokens=self.adapter.completion_tokens if self.adapter is not None else 0,
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


