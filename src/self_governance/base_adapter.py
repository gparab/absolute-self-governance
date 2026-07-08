from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
from self_governance.models import Agent

class BaseExecutionAdapter(ABC):
    """
    Abstract Base Class defining the pipeline execution contract.
    Decouples governance algorithms from concrete agent runtimes.
    """
    
    @abstractmethod
    def plan_task(self, task_description: str) -> Dict[str, Any]:
        """Decompose the task description into a structured graph of steps."""
        pass

    @abstractmethod
    def execute_development(self, agents: List[Agent], plan: Dict[str, Any]) -> Dict[str, Any]:
        """Execute source code modifications based on the plan."""
        pass

    @abstractmethod
    def review_code(self, agents: List[Agent], changes: Dict[str, Any]) -> Dict[str, Any]:
        """Review the modified code for structural or semantic issues."""
        pass

    @abstractmethod
    def execute_tests(self, agents: List[Agent], changes: Dict[str, Any], test_target: Optional[str] = None) -> Dict[str, Any]:
        """Execute test suites (e.g. pytest) and return outcomes/tracebacks."""
        pass

    @abstractmethod
    def run_security_scan(self, agents: List[Agent], changes: Dict[str, Any]) -> Dict[str, Any]:
        """Scan changes for vulnerabilities (e.g. bandit/semgrep rules)."""
        pass

    @abstractmethod
    def generate_documentation(self, agents: List[Agent], changes: Dict[str, Any]) -> Dict[str, Any]:
        """Generate/update docstrings, changelogs, or README files."""
        pass
