import os
import json
import time
import logging
from typing import List, Dict, Any
from self_governance.execution import dispatch_swarm_execution
from self_governance.models import Agent

logger = logging.getLogger("self_governance.benchmark")

def load_benchmark_tasks() -> List[Dict[str, Any]]:
    """Loads benchmark challenges from the JSON config."""
    tasks_path = os.path.join(os.path.dirname(__file__), "benchmark_tasks.json")
    with open(tasks_path, "r", encoding="utf-8") as f:
        return json.load(f)

def run_benchmark(api_key: str = None) -> Dict[str, Any]:
    """Runs the diagnostic code challenges under baseline and ASG modes."""
    tasks = load_benchmark_tasks()
    results = {}

    for task in tasks:
        task_id = task["id"]
        logger.info("Starting evaluation for benchmark task: %s", task["name"])
        
        # 1. Run Baseline (Direct Single-Agent Code Gen)
        baseline_metrics = run_baseline_mode(task, api_key)
        
        # 2. Run ASG (Deliberation, Entropy Sizing, Multi-Agent Loop)
        asg_metrics = run_asg_mode(task, api_key)
        
        results[task_id] = {
            "name": task["name"],
            "baseline": baseline_metrics,
            "asg": asg_metrics
        }
        
    return results

def run_baseline_mode(task: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    """Simulates a baseline run with direct, single-step generation."""
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    
    start_time = time.time()
    adapter = GeminiExecutionAdapter(api_key=api_key)
    
    plan = {"task": task["description"]}
    
    # Direct code execution
    exec_res = adapter.execute_development([], plan)
    written_files = exec_res.get("written_files", [])
    
    # Create test file on disk
    test_filepath = f"bench_test_{task['target_file']}"
    with open(test_filepath, "w", encoding="utf-8") as f:
        f.write(task["test_code"])
        
    # Run tests on host
    test_res = adapter.execute_tests([], {}, test_target=test_filepath)
    passed = test_res.get("status") == "completed"
    
    # Cleanup files
    for f_path in written_files:
        try:
            os.remove(f_path)
        except Exception:
            pass
    try:
        os.remove(test_filepath)
    except Exception:
        pass
        
    latency = time.time() - start_time
    # Gemini 2.5 Flash pricing: $0.075 / 1M input, $0.30 / 1M output
    cost = (adapter.prompt_tokens * 0.000000075) + (adapter.completion_tokens * 0.0000003)
    
    return {
        "passed": passed,
        "latency_sec": round(latency, 2),
        "estimated_cost_usd": round(cost, 6)
    }

def run_asg_mode(task: Dict[str, Any], api_key: str) -> Dict[str, Any]:
    """Simulates the ASG run with consensus deliberation, swarm sizing, and multi-agent pipeline."""
    from self_governance.consensus import run_consensus
    from self_governance.dimensioning import dimension_swarm
    from self_governance.gemini_adapter import GeminiExecutionAdapter
    
    start_time = time.time()
    
    # Deliberate candidate selection
    consensus_res = run_consensus(
        initial_roster=["agent_dev", "agent_tester", "agent_security"],
        initial_temp=1.0,
        target_tau=8.0
    )
    
    # Dynamic swarm sizing using Shannon entropy sizing rules
    req_vector = [0.8, 0.5, 0.7, 0.4]
    matrix = [[1.0, 0.0, 0.0, 0.0], [0.0, 1.0, 0.0, 0.0], [0.0, 0.0, 1.0, 0.0], [0.0, 0.0, 0.0, 1.0]]
    swarm_spec = dimension_swarm(req_vector, matrix)
    
    # Convert consensus results into Agent schemas
    agents = [Agent(role=r, prompt=f"Guide: {r}", capabilities=[]) for r in consensus_res.approved_roster]
    
    # Execute through hardened adapter
    adapter = GeminiExecutionAdapter(api_key=api_key)
    plan = {"task": task["description"]}
    exec_res = adapter.execute_development(agents, plan)
    written_files = exec_res.get("written_files", [])
    
    # Create test file on disk
    test_filepath = f"bench_test_{task['target_file']}"
    with open(test_filepath, "w", encoding="utf-8") as f:
        f.write(task["test_code"])
        
    # Run linter and security scan checks
    adapter.review_code(agents, exec_res)
    adapter.run_security_scan(agents, exec_res)
    
    # Run test verification sandbox
    test_res = adapter.execute_tests(agents, {}, test_target=test_filepath)
    passed = test_res.get("status") == "completed"
    
    # Cleanup files
    for f_path in written_files:
        try:
            os.remove(f_path)
        except Exception:
            pass
    try:
        os.remove(test_filepath)
    except Exception:
        pass
        
    latency = time.time() - start_time
    cost = (adapter.prompt_tokens * 0.000000075) + (adapter.completion_tokens * 0.0000003)
    
    return {
        "passed": passed,
        "latency_sec": round(latency, 2),
        "estimated_cost_usd": round(cost, 6)
    }
