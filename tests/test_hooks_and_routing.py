import sys
import json
import time
import pytest
from unittest.mock import patch
from self_governance.nudger import ResilientHookExecutor
from self_governance.economics import route_model
from self_governance.db import SessionLocal, Milestone, TokenUsage, AgentMemory
from self_governance.cli import main


def test_resilient_hooks(tmp_path):
    # Setup a dummy hooks folder inside working directory
    hooks_dir = tmp_path / "hooks"
    hooks_dir.mkdir(parents=True, exist_ok=True)

    # 1. Success hook
    hook_success = hooks_dir / "PreToolUse.sh"
    hook_success.write_text("#!/bin/sh\necho '{\"permission\": \"allow\", \"status\": \"ok\"}'\n")
    hook_success.chmod(0o755)

    # 2. Crash hook (non-zero exit code)
    hook_crash = hooks_dir / "PostToolUse.sh"
    hook_crash.write_text("#!/bin/sh\nexit 1\n")
    hook_crash.chmod(0o755)

    # 3. Timeout hook
    hook_timeout = hooks_dir / "PreCompact.sh"
    hook_timeout.write_text("#!/bin/sh\nsleep 10\n")
    hook_timeout.chmod(0o755)

    executor = ResilientHookExecutor(working_directory=str(tmp_path))

    # Verify PreToolUse succeeds and allows permission
    res_success = executor.execute_hook("PreToolUse", {"test": "data"})
    assert res_success["permission"] == "allow"
    assert res_success["status"] == "executed"
    assert res_success["exit_code"] == 0

    # Verify PostToolUse (crash hook) is caught and does not raise
    res_crash = executor.execute_hook("PostToolUse", {"test": "data"})
    assert res_crash["permission"] == "allow"
    assert res_crash["exit_code"] == 1

    # Verify PreCompact (timeout hook) is caught and does not raise, and times out safely
    start_time = time.time()
    res_timeout = executor.execute_hook("PreCompact", {"test": "data"})
    elapsed = time.time() - start_time
    assert elapsed < 7.0  # Must be terminated around 5.0 seconds
    assert res_timeout["permission"] == "allow"
    assert res_timeout["status"] == "error" or "timeout" in res_timeout.get("error_message", "").lower()

    # Verify non-existent hook handles gracefully
    res_nonexistent = executor.execute_hook("NonExistentHook", {})
    assert res_nonexistent["permission"] == "allow"
    assert res_nonexistent["status"] == "no_hook_configured"


def test_ast_based_routing():
    # Tier 1: Low complexity (simple variable assignments, low loop count, few nodes)
    low_comp_1 = "x = 1\ny = 2\nprint(x + y)"
    low_comp_2 = "for i in range(5):\n    print(i)"
    
    assert route_model("routing", code_snippet=low_comp_1) == "gemini-1.5-flash"
    assert route_model("routing", code_snippet=low_comp_2) == "gemini-1.5-flash"

    # Tier 2: Medium complexity (typical classes, multiple functions, error handling)
    med_comp_class = """
class AgentA:
    def __init__(self):
        self.val = 1
"""
    med_comp_funcs = """
def func_a():
    return 1
def func_b():
    return 2
"""
    med_comp_try = """
try:
    x = 1 / 0
except ZeroDivisionError:
    pass
"""
    assert route_model("routing", code_snippet=med_comp_class) == "gemini-1.5-pro"
    assert route_model("routing", code_snippet=med_comp_funcs) == "gemini-1.5-pro"
    assert route_model("routing", code_snippet=med_comp_try) == "gemini-1.5-pro"

    # Tier 3: High complexity (concurrent modules, network operations, cryptography/security, or >150 nodes)
    high_comp_concurrent = "import asyncio\nasync def run():\n    await asyncio.sleep(1)"
    high_comp_network = "import urllib.request\nurllib.request.urlopen('http://example.com')"
    high_comp_crypto = "import hashlib\nh = hashlib.sha256(b'test')"
    
    # Large code snippet (>150 nodes)
    large_code = "\n".join(f"v_{i} = {i}" for i in range(160))

    assert route_model("routing", code_snippet=high_comp_concurrent) == "gemini-2.5-pro"
    assert route_model("routing", code_snippet=high_comp_network) == "gemini-2.5-pro"
    assert route_model("routing", code_snippet=high_comp_crypto) == "gemini-2.5-pro"
    assert route_model("routing", code_snippet=large_code) == "gemini-2.5-pro"

    # Fallback to keyword-based routing when not code
    assert route_model("routine lint task") == "gemini-1.5-flash"
    assert route_model("complex succession planning task") == "gemini-1.5-pro"


def test_cli_save_restore_session(tmp_path):
    from self_governance.db import init_db
    init_db()
    # Setup some DB state to serialize
    db = SessionLocal()
    try:
        # Clear existing
        db.query(Milestone).delete()
        db.query(TokenUsage).delete()
        db.query(AgentMemory).delete()
        db.commit()

        # Add mock milestone
        m1 = Milestone(name="m1_test", status="PENDING", dependencies="[]")
        db.add(m1)
        # Add mock token usage
        tu = TokenUsage(tenant_id="default", prompt_tokens=10, completion_tokens=5, cost_usd=0.0123)
        db.add(tu)
        # Add mock topology memory
        mem = AgentMemory(key="active_topology_mesh", agent_id="agent_A", value="MESH_DATA")
        db.add(mem)
        db.commit()
    finally:
        db.close()

    session_file = tmp_path / "asg_session.json"

    # Test save-session command
    test_save_args = [
        "self-governance",
        "save-session",
        "--file",
        str(session_file),
        "--workdir",
        str(tmp_path)
    ]

    with patch.object(sys, "argv", test_save_args):
        main()

    assert session_file.exists()
    with open(session_file, "r", encoding="utf-8") as f:
        data = json.load(f)

    assert data["wallet"]["spent"] == pytest.approx(0.0123)
    assert len(data["pending_milestones"]) == 1
    assert data["pending_milestones"][0]["name"] == "m1_test"
    assert any("topology" in mem["key"] for mem in data["cached_metadata"]["memories"])

    # Clear DB to verify restore
    db = SessionLocal()
    try:
        db.query(Milestone).delete()
        db.query(TokenUsage).delete()
        db.query(AgentMemory).delete()
        db.commit()
    finally:
        db.close()

    # Test restore-session command
    test_restore_args = [
        "self-governance",
        "restore-session",
        "--file",
        str(session_file),
        "--workdir",
        str(tmp_path)
    ]

    with patch.object(sys, "argv", test_restore_args):
        main()

    # Verify restored state
    db = SessionLocal()
    try:
        restored_milestones = db.query(Milestone).all()
        restored_usages = db.query(TokenUsage).all()
        restored_memories = db.query(AgentMemory).all()

        assert len(restored_milestones) == 1
        assert restored_milestones[0].name == "m1_test"

        assert len(restored_usages) == 1
        assert restored_usages[0].cost_usd == pytest.approx(0.0123)

        assert len(restored_memories) == 1
        assert restored_memories[0].key == "active_topology_mesh"
    finally:
        db.close()
