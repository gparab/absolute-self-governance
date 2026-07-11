"""Base Execution Adapter module.

Defines the abstract interface for execution adapters, decoupling governance logic
from concrete agent or LLM runtime implementations.
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from self_governance.models import Agent


class BaseExecutionAdapter(ABC):
    """Abstract Base Class defining the pipeline execution contract.

    Decouples governance algorithms from concrete agent runtimes.
    """

    def is_reasoning_model(self, model_name: Optional[str]) -> bool:
        """Checks if the given model name corresponds to a reasoning model.

        Reasoning models include o1, o3, and thinking/reasoning models.
        """
        if not model_name:
            return False
        name_lower = model_name.lower()
        return any(x in name_lower for x in ("o1", "o3", "r1", "thinking", "reasoning"))

    def __init__(self) -> None:
        """Initializes the base execution adapter and registers dynamic tools."""
        self.tools: Dict[str, Any] = {}
        self.register_dynamic_tool("load_ability", self.load_ability)

    def register_dynamic_tool(self, name: str, func: Any) -> None:
        """Dynamically registers a tool/method to the adapter."""
        self.tools[name] = func

    def call_dynamic_tool(self, name: str, *args: Any, **kwargs: Any) -> Any:
        """Calls a registered dynamic tool by name."""
        if name in self.tools:
            return self.tools[name](*args, **kwargs)
        raise ValueError(f"Tool '{name}' is not registered.")

    def load_ability(self, ability_name: str, agent: Any) -> bool:
        """Loads capability instructions from src/self_governance/abilities/ and appends them to the agent."""
        from self_governance.ability_loader import AbilityLoader
        loader = AbilityLoader()
        return loader.load_ability(ability_name, agent)

    @abstractmethod
    def plan_task(self, task_description: str) -> Dict[str, Any]:
        """Decompose the task description into a structured graph of steps.

        Args:
            task_description: A description of the task to be planned.

        Returns:
            A dictionary containing the structured plan steps.
        """
        pass

    @abstractmethod
    def execute_development(
        self, agents: List[Agent], plan: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Execute source code modifications based on the plan.

        Args:
            agents: The list of Agent objects representing the workspace roster.
            plan: The structured plan dictionary.

        Returns:
            A dictionary containing the development task execution results.
        """
        pass

    @abstractmethod
    def review_code(
        self, agents: List[Agent], changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Review the modified code for structural or semantic issues.

        Args:
            agents: The list of Agent objects performing the review.
            changes: A dictionary detailing the code modifications.

        Returns:
            A dictionary containing the review outcomes.
        """
        pass

    @abstractmethod
    def execute_tests(
        self,
        agents: List[Agent],
        changes: Dict[str, Any],
        test_target: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute test suites (e.g. pytest) and return outcomes/tracebacks.

        Args:
            agents: The list of Agent objects executing the tests.
            changes: A dictionary detailing the code modifications.
            test_target: Optional path or label of the specific test target.

        Returns:
            A dictionary containing the test suite run results.
        """
        pass

    @abstractmethod
    def run_security_scan(
        self, agents: List[Agent], changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Scan changes for vulnerabilities (e.g. bandit/semgrep rules).

        Args:
            agents: The list of Agent objects executing the security scan.
            changes: A dictionary detailing the code modifications.

        Returns:
            A dictionary containing the security scan findings.
        """
        pass

    @abstractmethod
    def generate_documentation(
        self, agents: List[Agent], changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        """Generate/update docstrings, changelogs, or README files.

        Args:
            agents: The list of Agent objects generating the docs.
            changes: A dictionary detailing the code modifications.

        Returns:
            A dictionary containing the documentation generation results.
        """
        pass

    @abstractmethod
    def consult_advisor(self, conversation_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Consult a higher-intelligence advisor model with the conversation history.

        Args:
            conversation_history: List of dictionaries representing the dialogue.

        Returns:
            A dictionary containing the advisor's response.
        """
        pass

    @abstractmethod
    def get_billing_metrics(self) -> Dict[str, float]:
        """Retrieve billing metrics (tokens, estimated cost) for this adapter.
        
        Returns:
            A dictionary with keys like 'prompt_tokens', 'completion_tokens',
            and 'estimated_cost_usd'.
        """
        pass

