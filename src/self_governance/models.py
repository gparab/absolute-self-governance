"""Data Models module.

Defines Pydantic representations for Agent roles and SwarmConfig configurations
along with session and pipeline status Enums.
"""

from enum import Enum
from pydantic import BaseModel, Field, SkipValidation
from typing import List, Dict, Any, Iterator, Tuple, Union, Sequence, Annotated, Optional


class SessionStatus(str, Enum):
    """Represents the execution status of a dynamic swarm session."""
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PipelineStatus(str, Enum):
    """Represents the lifecycle status of a workflow pipeline execution."""
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class PersonaQualityGate(BaseModel):
    """Quality gate that a persona's vote must pass before counting toward consensus.

    Attributes:
        min_confidence: Minimum score (1.0–10.0) a persona must assign for the
            vote to count. Votes below this are abstentions.
        require_evidence: If True, the justification string must contain a
            file-colon-line reference (e.g. 'consensus.py:394') to count.
        false_positive_exclusions: Domain-specific patterns that, if found in
            the justification, cause the vote to be suppressed as a known
            false positive. Checked as case-insensitive substring matches.
    """

    min_confidence: float = Field(default=0.0, ge=0.0, le=10.0)
    require_evidence: bool = False
    false_positive_exclusions: List[str] = Field(default_factory=list)

    def passes(self, score: float, justification: str) -> bool:
        """Returns True if this vote passes the quality gate.

        Args:
            score: The numeric score assigned by the persona (1.0–10.0).
            justification: The textual justification accompanying the score.

        Returns:
            True if the vote should count toward consensus, False if it is
            suppressed as an abstention.
        """
        if score < self.min_confidence:
            return False
        if self.require_evidence:
            import re
            # Expect a file:line reference like 'foo.py:42' or 'consensus.py:394'
            if not re.search(r'\w+\.py:\d+', justification):
                return False
        jl = justification.lower()
        for pattern in self.false_positive_exclusions:
            if pattern.lower() in jl:
                return False
        return True


class Agent(BaseModel):
    """Represents an individual agent configured within the factory swarm.

    Attributes:
        role: Primary title or function of the agent.
        prompt: System prompt instructing LLM behavior.
        capabilities: List of technical capacity strings.
        developer_message: Optional stripped system prompt for reasoning models.
    """

    role: str
    prompt: str
    capabilities: List[str] = Field(default_factory=list)
    quality_gate: Optional[PersonaQualityGate] = None
    developer_message: Optional[str] = None

    def __init__(self, *args, **kwargs) -> None:
        """Initializes the Agent, supporting positional initialization.

        Args:
            *args: Positional values mapping to role, prompt, capabilities.
            **kwargs: Keyword values for model fields.
        """
        if args:
            field_names = ["role", "prompt", "capabilities", "quality_gate", "developer_message"]
            for name, val in zip(field_names, args):
                kwargs[name] = val
        super().__init__(**kwargs)

    def __getitem__(self, key: str) -> Any:
        """Accesses an agent attribute via dict subscript key.

        Args:
            key: Field name key.

        Returns:
            The associated field value.

        Raises:
            KeyError: If key is not a valid Agent field name.
        """
        if key in ("role", "prompt", "capabilities", "quality_gate", "developer_message"):
            return getattr(self, key)
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        """Sets an agent attribute value via dict subscript key.

        Args:
            key: Field name key.
            value: Value to store.

        Raises:
            KeyError: If key is not a valid Agent field name.
        """
        if key in ("role", "prompt", "capabilities", "quality_gate", "developer_message"):
            setattr(self, key, value)
        else:
            raise KeyError(key)

    def __delitem__(self, key: str) -> None:
        """Prevents deletion of required model parameters.

        Args:
            key: Field name key.

        Raises:
            TypeError: If attempting to delete a valid core field.
            KeyError: If key is not valid.
        """
        if key in ("role", "prompt", "capabilities", "quality_gate", "developer_message"):
            raise TypeError(f"Cannot delete core attribute '{key}' from Agent.")
        raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        """Determines if a field name exists in the Agent model.

        Args:
            key: Target field name key.

        Returns:
            True if the key is valid.
        """
        return key in ("role", "prompt", "capabilities", "quality_gate", "developer_message")

    def keys(self) -> List[str]:
        """Lists the valid model property names.

        Returns:
            List of string field names.
        """
        return ["role", "prompt", "capabilities", "quality_gate", "developer_message"]

    def values(self) -> List[Any]:
        """Lists the attribute values corresponding to model keys.

        Returns:
            List of values.
        """
        return [self.role, self.prompt, self.capabilities, self.quality_gate, self.developer_message]

    def items(self) -> List[Tuple[str, Any]]:
        """Returns the dictionary-like key-value tuple list of fields.

        Returns:
            List of (key, value) tuples.
        """
        return [
            ("role", self.role),
            ("prompt", self.prompt),
            ("capabilities", self.capabilities),
            ("quality_gate", self.quality_gate),
            ("developer_message", self.developer_message),
        ]

    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        """Iterates over the field keys of the Agent.

        Returns:
            An iterator of strings.
        """
        return iter(self.keys())

    def __len__(self) -> int:
        """Returns the fixed number of core model properties.

        Returns:
            The integer 5.
        """
        return 5

    def dict(self, *args, **kwargs) -> Dict[str, Any]:
        """Serializes the Agent instance to a plain dictionary.

        Args:
            *args: Passthrough arguments.
            **kwargs: Passthrough kwargs.

        Returns:
            Dictionary of model properties.
        """
        return self.model_dump(*args, **kwargs)


class SwarmConfig(BaseModel):
    """Configuration definition holding the dynamic roster of agents.

    Attributes:
        swarm: Sequence of active Agent configurations.
        hierarchical_swarms: Optional sub-swarms mapped by domain (Path C).
    """

    swarm: Annotated[Union[List[Agent], Sequence[Agent]], SkipValidation]
    hierarchical_swarms: Optional[Dict[str, 'SwarmConfig']] = None

    def __init__(self, *args, **kwargs) -> None:
        """Initializes the SwarmConfig model.

        Args:
            *args: Optional positional arguments (list of agents).
            **kwargs: Optional keyword field mappings.
        """
        if args:
            kwargs["swarm"] = args[0]
        super().__init__(**kwargs)

    def __getitem__(self, key: str) -> Any:
        """Accesses configuration attributes using dictionary subscript key.

        Args:
            key: Name of the property.

        Returns:
            Value of the property.

        Raises:
            KeyError: If key is not valid.
        """
        if key == "swarm":
            if not hasattr(self, "swarm"):
                raise KeyError("swarm")
            return self.swarm
        if key == "hierarchical_swarms":
            if not hasattr(self, "hierarchical_swarms"):
                raise KeyError("hierarchical_swarms")
            return self.hierarchical_swarms
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        """Modifies config attributes using dictionary subscript key.

        Args:
            key: Name of the property.
            value: Roster value to assign.

        Raises:
            KeyError: If key is not valid.
        """
        if key == "swarm":
            self.swarm = value
        elif key == "hierarchical_swarms":
            self.hierarchical_swarms = value
        else:
            raise KeyError(key)

    def __delitem__(self, key: str) -> None:
        """Deletes config attributes using dictionary subscript key.

        Args:
            key: Name of the property.

        Raises:
            KeyError: If key is not valid.
        """
        if key == "swarm":
            if hasattr(self, "swarm"):
                delattr(self, "swarm")
        else:
            raise KeyError(key)

    def __getattr__(self, name: str) -> Any:
        """Fallback to raise AttributeError for invalid properties.

        Args:
            name: Attribute name requested.

        Raises:
            AttributeError: Always raised.
        """
        raise AttributeError(
            f"'{self.__class__.__name__}' object has no attribute '{name}'"
        )

    def __contains__(self, key: str) -> bool:
        """Checks if property key exists in config.

        Args:
            key: Property string key.

        Returns:
            True if the key exists.
        """
        return key == "swarm" and hasattr(self, key)

    def keys(self) -> List[str]:
        """Lists key field names.

        Returns:
            List containing ["swarm"].
        """
        return ["swarm"] if hasattr(self, "swarm") else []

    def values(self) -> List[Any]:
        """Lists attribute values.

        Returns:
            List containing the swarm roster.
        """
        return [self.swarm] if hasattr(self, "swarm") else []

    def items(self) -> List[Tuple[str, Any]]:
        """Returns the dictionary key-value list.

        Returns:
            List of key-value tuples.
        """
        return [("swarm", self.swarm)] if hasattr(self, "swarm") else []

    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        """Iterates over keys.

        Returns:
            An iterator of strings.
        """
        return iter(self.keys())

    def __len__(self) -> int:
        """Returns the count of config properties.

        Returns:
            Size of key list.
        """
        return len(self.keys())

    def dict(self, *args, **kwargs) -> Dict[str, Any]:
        """Serializes the SwarmConfig instance to a plain dictionary.

        Defensively limits serialization size to prevent OOM on large rosters.

        Args:
            *args: Passthrough serialization arguments.
            **kwargs: Passthrough serialization kwargs.

        Returns:
            Serialized dictionary representation.
        """
        if not hasattr(self, "swarm"):
            return {}
        if len(self.swarm) <= 1000:
            return {"swarm": [a.dict(*args, **kwargs) for a in self.swarm]}
        # Avoid serializing individual items if too large to prevent OOM
        return {"swarm": self.swarm}

    def model_dump(self, *args, **kwargs) -> Dict[str, Any]:
        """Alias method to serialize the SwarmConfig to a dict.

        Args:
            *args: Passthrough arguments.
            **kwargs: Passthrough kwargs.

        Returns:
            Serialized dictionary representation.
        """
        return self.dict(*args, **kwargs)


class PipelinePhase(str, Enum):
    """Lifecycle phases of the ASG pipeline."""

    PLAN = "plan"
    BUILD = "build"
    REVIEW = "review"
    QA = "qa"
    SHIP = "ship"
    RETRO = "retro"


class PipelineArtifact(BaseModel):
    """Structured record of one succession session's decisions and context.

    Each succession session writes one PipelineArtifact. The nudger reads
    the previous artifact at startup to give the council prior context and
    prevent repeat deliberations.

    Attributes:
        phase: Pipeline phase that produced this artifact.
        author_persona: Role name of the persona that authored the session summary.
        timestamp: ISO-8601 timestamp of when consensus completed.
        approved_roster: Role names that passed consensus.
        final_temperature: TETD temperature when consensus was reached.
        final_threshold: TETD threshold when consensus was reached.
        cycles_needed: Number of TETD iterations consumed.
        decisions: Key decisions made during this session.
        open_questions: Unresolved questions for the next phase.
        next_context: Prose summary to prime the next succession session.
    """

    phase: PipelinePhase = PipelinePhase.BUILD
    author_persona: str = "Orchestrator"
    timestamp: str = Field(
        default_factory=lambda: __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).isoformat()
    )
    approved_roster: List[str] = Field(default_factory=list)
    final_temperature: float = 1.0
    final_threshold: float = 9.0
    cycles_needed: int = 1
    decisions: List[str] = Field(default_factory=list)
    open_questions: List[str] = Field(default_factory=list)
    next_context: str = ""
