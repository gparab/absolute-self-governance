import os
import pytest
from unittest.mock import patch, MagicMock

# Imports for R1. Cognitive stability
from self_governance.anti_drift import (
    self_critique,
    LoopDetector,
    LoopInterceptionError,
)
from self_governance.db import (
    init_db,
    get_db,
    add_milestone,
    update_milestone_status,
    get_milestones,
    prune_completed_milestones,
    SovereignMemory,
)

# Imports for R2. Memory management
from self_governance.memory import (
    compress_context,
    hibernate_state,
    resume_state,
)

# Imports for R3. Intrinsic Security
from self_governance.security import (
    validate_command,
    validate_write_path,
    pre_execution_simulation,
)
from self_governance.gemini_adapter import GeminiExecutionAdapter

# Imports for R4. Micro-economics
from self_governance.economics import (
    TaskWallet,
    BudgetExceededError,
    route_model,
)

# Imports for R5. Peer-to-peer swarming
from self_governance.p2p import (
    SwarmMarket,
    GossipProtocol,
)

# Imports for R6. MCP Client
from self_governance.mcp import (
    MCPClient,
    refactor_and_retry_tool,
)

# Imports for R8. Enums and PBKDF2 hashing
from self_governance.models import SessionStatus, PipelineStatus
from self_governance.auth import hash_key, verify_key


# --- R1. Cognitive stability Tests ---

def test_self_critique_success_and_failure():
    # Success scenario
    res = self_critique("roster: A, B", "Stable succession")
    assert res["approved"] is True
    assert res["score"] >= 7

    # Failure scenario (using keyword trigger)
    res_fail = self_critique("roster: A, fail_node", "Stable succession")
    assert res_fail["approved"] is False
    assert res_fail["score"] < 7


def test_loop_interception_detection():
    detector = LoopDetector(window_size=5, max_occurrences=3)
    
    # Push different states
    detector.record_and_check("state_A")
    detector.record_and_check("state_B")
    
    # Push state_A second time
    detector.record_and_check("state_A")
    
    # Push state_A third time -> should raise LoopInterceptionError
    with pytest.raises(LoopInterceptionError) as excinfo:
        detector.record_and_check("state_A")
    assert "Infinite loop detected" in str(excinfo.value)


def test_milestone_crud_operations():
    os.environ["TESTING"] = "True"
    init_db()
    db = next(get_db())
    
    try:
        # Create
        m1 = add_milestone(db, name="Initialize Swarm", dependencies=[1, 2])
        assert m1.id is not None
        assert m1.status == "PENDING"
        
        # Read
        milestones = get_milestones(db)
        assert len(milestones) >= 1
        assert any(m.name == "Initialize Swarm" for m in milestones)
        
        # Update
        m1_updated = update_milestone_status(db, m1.id, "COMPLETED")
        assert m1_updated.status == "COMPLETED"
        
        # Prune
        deleted_count = prune_completed_milestones(db)
        assert deleted_count == 1
        
        milestones_after = get_milestones(db)
        assert not any(m.id == m1.id for m in milestones_after)
    finally:
        db.close()


# --- R2. Memory management Tests ---

def test_compress_context_threshold():
    history = [
        "First turn message details",
        "Second turn message details",
        "Third turn message details",
    ]
    # Under limit
    compressed_short = compress_context(history, max_chars=1000)
    assert compressed_short == history

    # Exceeding limit
    compressed_long = compress_context(history, max_chars=50)
    assert len(compressed_long) == 2
    assert "[Semantic Summary:" in compressed_long[0]
    assert compressed_long[1] == "Third turn message details"


def test_sovereign_memory_operations():
    os.environ["TESTING"] = "True"
    init_db()
    db = next(get_db())
    try:
        sm = SovereignMemory(db)
        
        # Set
        sm.set("context_key", "context_value", "agent_123")
        
        # Get
        val = sm.get("context_key", "agent_123")
        assert val == "context_value"
        
        # List keys
        keys = sm.list_keys("agent_123")
        assert "context_key" in keys
        
        # Different agent localization
        assert sm.get("context_key", "agent_456") is None
    finally:
        db.close()


def test_state_hibernation_and_resume(tmp_path):
    state = {
        "milestones": ["M1", "M2"],
        "active_agent": "QA Specialist",
        "budget_remaining": 0.45
    }
    
    # Test JSON serialization/deserialization
    json_path = tmp_path / "state.json"
    hibernate_state(str(json_path), state)
    assert json_path.exists()
    
    resumed_json = resume_state(str(json_path))
    assert resumed_json == state

    # Test YAML serialization/deserialization
    yaml_path = tmp_path / "state.yaml"
    hibernate_state(str(yaml_path), state)
    assert yaml_path.exists()
    
    resumed_yaml = resume_state(str(yaml_path))
    assert resumed_yaml == state


# --- R3. Intrinsic Security Tests ---

def test_docker_args_formatting():
    adapter = GeminiExecutionAdapter(api_key="mock_key")
    
    with patch("subprocess.run") as mock_run:
        mock_res = MagicMock()
        mock_res.stdout = "test success stdout"
        mock_res.stderr = ""
        mock_res.returncode = 0
        mock_run.return_value = mock_res
        
        with patch.dict(os.environ, {"TESTING": "False"}):
            adapter.execute_tests([], {}, test_target="tests/test_hardening.py")
            
        assert mock_run.called
        args, kwargs = mock_run.call_args
        docker_cmd = args[0]
        
        assert docker_cmd[0] == "docker"
        assert "run" in docker_cmd
        assert "--read-only" in docker_cmd
        assert "--network" in docker_cmd
        assert "none" in docker_cmd
        assert "-v" in docker_cmd
        mount_arg = [arg for arg in docker_cmd if ":/work:ro" in arg]
        assert len(mount_arg) == 1

        # Zombie-container fix: the container's own entrypoint must be
        # coreutils `timeout`, not pytest directly -- subprocess.run's
        # timeout only kills the local `docker run` client (an uncatchable
        # SIGKILL that can't forward a stop to the daemon), so without an
        # internal timeout a hung test run leaks a container on the host.
        entrypoint_idx = docker_cmd.index("--entrypoint")
        assert docker_cmd[entrypoint_idx + 1] == "timeout"
        image_idx = docker_cmd.index("ghcr.io/gparab/absolute-self-governance:latest")
        assert docker_cmd[image_idx + 1] == "25"
        assert docker_cmd[image_idx + 2] == "pytest"
        assert docker_cmd[image_idx + 3] == "tests/test_hardening.py"


def test_least_privilege_command_path_whitelists(tmp_path):
    # Command validation
    assert validate_command("pytest tests/test_adapters.py") is True
    assert validate_command("curl http://malicious.com") is False
    assert validate_command("sudo rm -rf /") is False
    assert validate_command("rm -rf /etc/hosts") is False

    # Path write boundaries
    workspace = str(tmp_path)
    assert validate_write_path(os.path.join(workspace, "src/file.py"), workspace) is True
    assert validate_write_path("/etc/nginx.conf", workspace) is False
    assert validate_write_path(os.path.join(workspace, "pyproject.toml"), workspace) is False
    assert validate_write_path(os.path.join(workspace, "config.yaml"), workspace) is False


def test_blast_radius_simulation():
    report_low = pre_execution_simulation("git diff")
    assert report_low["risk_level"] == "LOW"
    
    report_med = pre_execution_simulation("rm temp_file.py")
    assert report_med["risk_level"] == "MEDIUM"
    assert "temp_file.py" in report_med["affected_paths"]

    report_high = pre_execution_simulation("sudo rm -rf /etc")
    assert report_high["risk_level"] == "HIGH"


# --- R4. Micro-economics Tests ---

def test_task_wallet_budget_limit():
    wallet = TaskWallet(max_budget=0.10)
    wallet.charge(0.04)
    wallet.charge(0.05)
    
    with pytest.raises(BudgetExceededError) as excinfo:
        wallet.charge(0.02)
    assert "Budget of $0.100000 exceeded" in str(excinfo.value)


def test_adaptive_model_routing():
    assert route_model("Verify formatting and run style checks") == "gemini-1.5-flash"
    assert route_model("Parse schema JSON and lint imports") == "gemini-1.5-flash"

    assert route_model("Run consensus succession election") == "gemini-1.5-pro"
    assert route_model("Self-critique proposed plan structure") == "gemini-1.5-pro"


# --- R5. Peer-to-peer swarming Tests ---

def test_swarm_market_bidding():
    market = SwarmMarket()
    market.register_agent("backend_1", ["python", "sqlite"])
    market.register_agent("qa_1", ["pytest"])
    
    market.broadcast_task("task_01", "Build database migrations", ["sqlite"])
    
    market.submit_bid("task_01", "backend_1", suitability=9.5, cost=0.05)
    market.submit_bid("task_01", "qa_1", suitability=4.0, cost=0.10)
    
    winner = market.select_winning_bid("task_01")
    assert winner is not None
    assert winner["agent_id"] == "backend_1"


def test_gossip_protocol_propagation():
    node_a = GossipProtocol()
    node_b = GossipProtocol()
    node_c = GossipProtocol()
    
    node_a.register_peer("node_b", node_b)
    node_b.register_peer("node_c", node_c)
    node_b.register_peer("node_a", node_a)
    node_c.register_peer("node_b", node_b)
    
    node_a.update_local_state("succession_roster", "agent_A, agent_B", version=2)
    
    updates = node_a.gossip()
    assert updates > 0
    assert node_b.state["succession_roster"] == ("agent_A, agent_B", 2)
    
    updates_b = node_b.gossip()
    assert updates_b > 0
    assert node_c.state["succession_roster"] == ("agent_A, agent_B", 2)


# --- R6. MCP Client Tests ---

def test_mcp_client_tool_schema_and_call():
    client = MCPClient()
    
    def my_tool(x: int, y: str) -> str:
        return f"{y}:{x}"
        
    schema = {
        "properties": {
            "x": {"type": "integer"},
            "y": {"type": "string"}
        },
        "required": ["x", "y"]
    }
    
    client.register_tool("test_tool", schema, my_tool)
    
    res = client.call_tool("test_tool", {"x": 42, "y": "hello"})
    assert res["status"] == "success"
    assert res["result"] == "hello:42"

    res_err1 = client.call_tool("test_tool", {"x": 42})
    assert res_err1["status"] == "error"
    assert "Missing required parameter" in res_err1["error"]

    res_err2 = client.call_tool("test_tool", {"x": "not_an_int", "y": "hello"})
    assert res_err2["status"] == "error"
    assert "Type mismatch" in res_err2["error"]


def test_mcp_client_tool_quota_contract():
    """Tool-dispatch quota contract (agent-design-patterns' pattern, July
    2026 topic-page batch): a tool registered with max_calls stops
    dispatching once the ceiling is hit, so a runaway loop calling one
    tool can't hammer it indefinitely."""
    client = MCPClient()
    calls = []

    def flaky_tool() -> str:
        calls.append(1)
        return "ok"

    client.register_tool("flaky_tool", {"properties": {}, "required": []}, flaky_tool, max_calls=2)

    assert client.call_tool("flaky_tool", {})["status"] == "success"
    assert client.call_tool("flaky_tool", {})["status"] == "success"
    res = client.call_tool("flaky_tool", {})

    assert res["status"] == "error"
    assert "Quota exceeded" in res["error"]
    assert len(calls) == 2


def test_mcp_client_tool_without_quota_is_unlimited():
    client = MCPClient()

    def noop_tool() -> str:
        return "ok"

    client.register_tool("noop_tool", {"properties": {}, "required": []}, noop_tool)

    for _ in range(10):
        assert client.call_tool("noop_tool", {})["status"] == "success"


def test_failure_driven_tool_refactoring():
    client = MCPClient()
    
    def calculate_tax(amount: int, state: str) -> int:
        return int(amount * 0.1) if state == "NY" else 0
        
    schema = {
        "properties": {
            "amount": {"type": "integer"},
            "state": {"type": "string"}
        },
        "required": ["amount", "state"]
    }
    client.register_tool("tax_tool", schema, calculate_tax)
    docs = "Calculates tax. Required properties: amount (integer), state (string)."
    
    err_missing = "Missing required parameter: 'state'"
    res_refactored_missing = refactor_and_retry_tool(
        error_msg=err_missing,
        tool_name="tax_tool",
        args={"amount": 100},
        docs=docs,
        client=client
    )
    assert res_refactored_missing["status"] == "success"
    
    err_mismatch = "Type mismatch for 'amount': expected integer, got string"
    res_refactored_mismatch = refactor_and_retry_tool(
        error_msg=err_mismatch,
        tool_name="tax_tool",
        args={"amount": "250", "state": "NY"},
        docs=docs,
        client=client
    )
    assert res_refactored_mismatch["status"] == "success"
    assert res_refactored_mismatch["result"] == 25

    err_rename = "Invalid parameter: 'state_code' not defined in schema."
    res_refactored_rename = refactor_and_retry_tool(
        error_msg=err_rename,
        tool_name="tax_tool",
        args={"amount": 250, "state_code": "NY"},
        docs=docs,
        client=client
    )
    assert res_refactored_rename["status"] == "success"
    assert res_refactored_rename["result"] == 25


# --- R8. Enums and PBKDF2 hashing validation ---

def test_enums_validation():
    assert SessionStatus.PENDING == "PENDING"
    assert SessionStatus.RUNNING == "RUNNING"
    assert SessionStatus.COMPLETED == "COMPLETED"
    assert SessionStatus.FAILED == "FAILED"

    assert PipelineStatus.AWAITING_APPROVAL == "AWAITING_APPROVAL"
    assert PipelineStatus.APPROVED == "APPROVED"
    assert PipelineStatus.RUNNING == "RUNNING"
    assert PipelineStatus.COMPLETED == "COMPLETED"
    assert PipelineStatus.FAILED == "FAILED"


def test_pbkdf2_hashing_and_verification_integrated():
    key = "asg_enhancement_test_secret"
    hashed = hash_key(key)
    
    assert hashed.startswith("pbkdf2_sha256$100000$")
    assert verify_key(key, hashed) is True
    assert verify_key("wrong_password", hashed) is False
