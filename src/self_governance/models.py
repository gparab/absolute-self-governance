from dataclasses import dataclass
from typing import List, Dict, Any, Iterator, Tuple

@dataclass
class Agent:
    """
    Represents an individual agent in the swarm.
    """
    role: str
    prompt: str

    def __getitem__(self, key: str) -> Any:
        if key == "role":
            if not hasattr(self, "role"):
                raise KeyError("role")
            return self.role
        elif key == "prompt":
            if not hasattr(self, "prompt"):
                raise KeyError("prompt")
            return self.prompt
        raise KeyError(key)

    def __setitem__(self, key: str, value: Any) -> None:
        if key == "role":
            self.role = value
        elif key == "prompt":
            self.prompt = value
        else:
            raise KeyError(key)

    def __delitem__(self, key: str) -> None:
        if key in ("role", "prompt"):
            if hasattr(self, key):
                delattr(self, key)
        else:
            raise KeyError(key)

    def __getattr__(self, name: str) -> Any:
        if name in ("role", "prompt"):
            raise KeyError(name)
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    def __contains__(self, key: str) -> bool:
        return key in ("role", "prompt") and hasattr(self, key)

    def keys(self) -> List[str]:
        keys_list = []
        if hasattr(self, "role"):
            keys_list.append("role")
        if hasattr(self, "prompt"):
            keys_list.append("prompt")
        return keys_list

    def values(self) -> List[Any]:
        vals = []
        if hasattr(self, "role"):
            vals.append(self.role)
        if hasattr(self, "prompt"):
            vals.append(self.prompt)
        return vals

    def items(self) -> List[Tuple[str, Any]]:
        items_list = []
        if hasattr(self, "role"):
            items_list.append(("role", self.role))
        if hasattr(self, "prompt"):
            items_list.append(("prompt", self.prompt))
        return items_list

    def __iter__(self) -> Iterator[str]:
        return iter(self.keys())

    def __len__(self) -> int:
        return len(self.keys())

    def dict(self) -> Dict[str, Any]:
        """
        Serialize Agent to a dictionary.
        """
        d = {}
        if hasattr(self, "role"):
            d["role"] = self.role
        if hasattr(self, "prompt"):
            d["prompt"] = self.prompt
        return d

    def model_dump(self) -> Dict[str, Any]:
        """
        Serialize Agent to a dictionary (alias for dict()).
        """
        return self.dict()


@dataclass
class SwarmConfig:
    """
    Configuration for the swarm of agents.
    """
    swarm: List[Agent]

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
        if name == "swarm":
            raise KeyError(name)
        raise AttributeError(f"'{self.__class__.__name__}' object has no attribute '{name}'")

    def __contains__(self, key: str) -> bool:
        return key == "swarm" and hasattr(self, key)

    def keys(self) -> List[str]:
        return ["swarm"] if hasattr(self, "swarm") else []

    def values(self) -> List[Any]:
        return [self.swarm] if hasattr(self, "swarm") else []

    def items(self) -> List[Tuple[str, Any]]:
        return [("swarm", self.swarm)] if hasattr(self, "swarm") else []

    def __iter__(self) -> Iterator[str]:
        return iter(self.keys())

    def __len__(self) -> int:
        return len(self.keys())

    def dict(self) -> Dict[str, Any]:
        """
        Serialize SwarmConfig to a dictionary.

        If the number of agents is 1000 or fewer, returns a dictionary with
        agents serialized to standard dicts. If the number of agents exceeds
        1000, returns the raw agents list to prevent OOM.

        Returns:
            A dictionary representation of the SwarmConfig.
        """
        if not hasattr(self, "swarm"):
            return {}
        if len(self.swarm) <= 1000:
            return {"swarm": [dict(a) for a in self.swarm]}
        return {"swarm": self.swarm}

    def model_dump(self) -> Dict[str, Any]:
        """
        Serialize SwarmConfig to a dictionary (alias for dict()).

        Returns:
            A dictionary representation of the SwarmConfig.
        """
        return self.dict()
