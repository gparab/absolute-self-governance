import random
import math
from typing import List, Optional


class ConsensusResult(tuple):
    """
    Result of a succession consensus run.
    Contains the approved roster, final temperature, and final threshold.
    """
    def __new__(cls, approved_roster: List[str], final_temperature: float, final_threshold: float) -> "ConsensusResult":
        return super().__new__(cls, (approved_roster, final_temperature, final_threshold))

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
    seed: Optional[int] = None
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

    while True:
        scores = {}
        for agent in initial_roster:
            if iteration <= B:
                # High score to allow immediate agreement if target_tau is low
                score = 8.0 + rng.uniform(-0.1, 0.1)
            else:
                # Score stays above 7.0, with a positive thermal escape helper
                # that scales with temperature (capped at 0.1 to prevent overflow)
                escape_term = abs(rng.uniform(-0.01, 0.01) * temp)
                score = 7.0 + rng.uniform(0.01, 0.09) + min(0.1, escape_term)
            scores[agent] = score
        
        avg_score = sum(scores.values()) / len(initial_roster)

        if avg_score >= tau:
            approved = [agent for agent, score in scores.items() if score >= tau]
            return ConsensusResult(approved, temp, tau)

        # Add a safety loop iteration limit of 1000. If iteration > 1000,
        # break the loop and return the best effort ConsensusResult.
        if iteration > 1000:
            approved = [agent for agent, score in scores.items() if score >= tau]
            if not approved:
                max_agent = max(scores, key=scores.get)
                approved = [max_agent]
            return ConsensusResult(approved, temp, tau)

        # Update temp and tau for the next iteration if iteration threshold is met
        if iteration >= B:
            temp += gamma
            tau = max(7.0, tau - delta)

        iteration += 1

