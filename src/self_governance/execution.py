import os
import logging
import time
from typing import List, Dict, Any, Optional
from self_governance.models import Agent
from self_governance.base_adapter import BaseExecutionAdapter

logger = logging.getLogger("self_governance.execution")


class MockExecutionAdapter(BaseExecutionAdapter):
    """
    A concrete execution adapter returning mock traces for testing.
    """

    def plan_task(self, task_description: str) -> Dict[str, Any]:
        return {
            "task": task_description,
            "steps": ["Design module structure", "Implement classes", "Verify tests"],
        }

    def execute_development(
        self, agents: List[Agent], plan: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "status": "completed",
            "output": "Code modifications implemented successfully.",
        }

    def review_code(
        self, agents: List[Agent], changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "status": "completed",
            "output": "Code reviewed. Code quality scores meet target standards.",
        }

    def execute_tests(
        self,
        agents: List[Agent],
        changes: Dict[str, Any],
        test_target: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "status": "completed",
            "output": "All test cases verify successful compilation.",
        }

    def run_security_scan(
        self, agents: List[Agent], changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "status": "completed",
            "output": "No security vulnerabilities identified.",
        }

    def generate_documentation(
        self, agents: List[Agent], changes: Dict[str, Any]
    ) -> Dict[str, Any]:
        return {
            "status": "completed",
            "output": "API references and docstrings updated.",
        }

    def consult_advisor(self, conversation_history: List[Dict[str, Any]]) -> Dict[str, Any]:
        return {
            "status": "completed",
            "output": "Mock Advisor Response: Recommended path is approved.",
            "stop_reason": "end_turn"
        }


def dispatch_swarm_execution(
    agents: List[Agent],
    task_description: str,
    adapter: Optional[BaseExecutionAdapter] = None,
) -> Dict[str, Any]:
    """
    Execute task requirements using the provided adapter.
    """
    if adapter is not None:
        exec_adapter = adapter
    else:
        if os.getenv("GEMINI_API_KEY"):
            from self_governance.gemini_adapter import GeminiExecutionAdapter

            exec_adapter = GeminiExecutionAdapter()
        else:
            exec_adapter = MockExecutionAdapter()
    logger.info(
        "Starting execution pipeline using adapter: %s", exec_adapter.__class__.__name__
    )

    start_time = time.time()

    # 1. Planning
    plan = exec_adapter.plan_task(task_description)

    # 2. Development
    dev_res = exec_adapter.execute_development(agents, plan)

    # 3. Review
    review_res = exec_adapter.review_code(agents, dev_res)

    # 4. Testing
    test_res = exec_adapter.execute_tests(agents, dev_res)

    # 5. Security
    sec_res = exec_adapter.run_security_scan(agents, dev_res)

    # 6. Documentation
    doc_res = exec_adapter.generate_documentation(agents, dev_res)

    duration = time.time() - start_time

    # Collect traces for backward compatibility
    traces = []
    for agent in agents:
        role = agent.role
        if "role_0" in role or "dev" in role:
            traces.append(
                {
                    "agent_role": role,
                    "status": dev_res["status"],
                    "output": dev_res["output"],
                }
            )
        elif "role_1" in role or "qa" in role:
            traces.append(
                {
                    "agent_role": role,
                    "status": test_res["status"],
                    "output": test_res["output"],
                }
            )
        else:
            traces.append(
                {
                    "agent_role": role,
                    "status": "completed",
                    "output": f"Execution output for {role}",
                }
            )

    return {
        "task": task_description,
        "duration_seconds": duration,
        "agent_count": len(agents),
        "plan": plan,
        "development": dev_res,
        "review": review_res,
        "testing": test_res,
        "security": sec_res,
        "documentation": doc_res,
        "traces": traces,
    }
