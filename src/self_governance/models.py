from pydantic import BaseModel, Field
from typing import List, Dict, Any, Iterator, Tuple


class Agent(BaseModel):
    """
    Represents an individual agent in the swarm.
    """

    role: str
    prompt: str
    capabilities: List[str] = Field(default_factory=list)

    def __init__(self, *args, **kwargs) -> None:
        if args:
            field_names = ["role", "prompt", "capabilities"]
            for name, val in zip(field_names, args):
                kwargs[name] = val
        super().__init__(**kwargs)

    def __getitem__(self, key: str) -> Any:
        if key in ("role", "prompt", "capabilities"):
            return getattr(self, key)
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key in ("role", "prompt", "capabilities"):
            setattr(self, key, value)
        else:
            raise KeyError(key)

    def __delitem__(self, key: str) -> None:
        if key in ("role", "prompt", "capabilities"):
            raise TypeError(f"Cannot delete core attribute '{key}' from Agent.")
        raise KeyError(key)

    def __contains__(self, key: str) -> bool:
        return key in ("role", "prompt", "capabilities")

    def keys(self) -> List[str]:
        return ["role", "prompt", "capabilities"]

    def values(self) -> List[Any]:
        return [self.role, self.prompt, self.capabilities]

    def items(self) -> List[Tuple[str, Any]]:
        return [
            ("role", self.role),
            ("prompt", self.prompt),
            ("capabilities", self.capabilities),
        ]

    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        # Deliberately dict-like (iterates keys, like a real Mapping), not
        # pydantic.BaseModel's own __iter__ (which yields (key, value) pairs
        # for internal dict(model) support). Locked in by
        # tests/test_coverage_boost.py's list(agent) == [...] assertion —
        # changing this would be a real behavior change, not a type fix.
        return iter(self.keys())

    def __len__(self) -> int:
        return 3

    def dict(self, *args, **kwargs) -> Dict[str, Any]:
        """
        Serialize Agent to a dictionary.
        """
        return self.model_dump(*args, **kwargs)


class SwarmConfig(BaseModel):
    """
    Configuration for the swarm of agents.
    """

    swarm: Any

    def __init__(self, *args, **kwargs) -> None:
        if args:
            kwargs["swarm"] = args[0]
        super().__init__(**kwargs)

    def __getitem__(self, key: str) -> Any:
        if key == "swarm":
            if not hasattr(self, "swarm"):
                raise KeyError("swarm")
            return self.swarm
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key == "swarm":
            self.swarm = value
        else:
            raise KeyError(key)

    def __delitem__(self, key: str) -> None:
        if key == "swarm":
            if hasattr(self, "swarm"):
                delattr(self, "swarm")
        else:
            raise KeyError(key)

    def __getattr__(self, name: str) -> Any:
        raise AttributeError(
            f"'{self.__class__.__name__}' object has no attribute '{name}'"
        )

    def __contains__(self, key: str) -> bool:
        return key == "swarm" and hasattr(self, key)

    def keys(self) -> List[str]:
        return ["swarm"] if hasattr(self, "swarm") else []

    def values(self) -> List[Any]:
        return [self.swarm] if hasattr(self, "swarm") else []

    def items(self) -> List[Tuple[str, Any]]:
        return [("swarm", self.swarm)] if hasattr(self, "swarm") else []

    def __iter__(self) -> Iterator[str]:  # type: ignore[override]
        # Deliberately dict-like (iterates keys, like a real Mapping), not
        # pydantic.BaseModel's own __iter__ (which yields (key, value) pairs
        # for internal dict(model) support). Locked in by
        # tests/test_coverage_boost.py's list(agent) == [...] assertion —
        # changing this would be a real behavior change, not a type fix.
        return iter(self.keys())

    def __len__(self) -> int:
        return len(self.keys())

    def dict(self, *args, **kwargs) -> Dict[str, Any]:
        """
        Serialize SwarmConfig to a dictionary.
        """
        if not hasattr(self, "swarm"):
            return {}
        if len(self.swarm) <= 1000:
            return {"swarm": [a.dict(*args, **kwargs) for a in self.swarm]}
        # Avoid serializing individual items if too large to prevent OOM
        return {"swarm": self.swarm}

    def model_dump(self, *args, **kwargs) -> Dict[str, Any]:
        """
        Serialize SwarmConfig to a dictionary.
        """
        return self.dict(*args, **kwargs)
