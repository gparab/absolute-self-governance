import os
import sys
import json
import yaml
import hmac
import hashlib
import runpy
import subprocess
import threading
import pytest
from unittest.mock import MagicMock, patch

# Import models and db
from self_governance.models import Agent, SwarmConfig
from self_governance.base_adapter import BaseExecutionAdapter
from self_governance.anti_drift import LoopDetector, self_critique
from self_governance.auth import get_current_tenant_id, authenticate_tenant
from self_governance.cli import main
from self_governance.consensus import ConsensusEngine, PBFTConsensusEngine
from self_governance.db import SovereignMemory, COWMemoryBranch, Tenant
from self_governance.devserver import _metric_value, dev_app
from self_governance.economics import analyze_ast_complexity, route_model
from self_governance.gemini_adapter import GeminiExecutionAdapter, call_safely, call_gemini_with_metadata
from self_governance.github_app import app, require_admin, _handle_issues_event
from self_governance.learning import (
    BinaryMinHeap, BinaryMaxHeap, HNSWIndex, AgentDB, SmartRetrievalPipeline, MemoryBridge,
    get_encryption_key, decrypt_data
)
from self_governance.mcp import MCPClient, refactor_and_retry_tool
from self_governance.memory import compress_context, resume_state
from self_governance.nudger import ContinuousNudger, ResilientHookExecutor, _emit_event
from self_governance.p2p import SwarmMarket, GossipProtocol, BoundedSet, EnhancedGossipProtocol
from self_governance.security import validate_command, pre_execution_simulation
from self_governance.shadow_logging import log_shadow_event, check_confidence_and_prompt
from self_governance.topology import SwarmTopology

# FastAPI TestClient
from fastapi.testclient import TestClient
from fastapi import HTTPException


# =====================================================================
# 1. anti_drift.py
# =====================================================================
def test_anti_drift_boost(monkeypatch):
    # LoopDetector history.pop(0) coverage
    detector = LoopDetector(window_size=3, max_occurrences=3)
    detector.record_and_check("state1")
    detector.record_and_check("state2")
    detector.record_and_check("state3")
    detector.record_and_check("state4")  # Triggers pop(0)
    assert len(detector.history) == 3

    # self_critique decision paths with Gemini
    monkeypatch.setenv("TESTING", "False")
    mock_adapter = MagicMock()
    mock_adapter.api_key = "dummy_api_key"
    mock_adapter.model_review = "gemini-2.5-flash"

    # Scenario A: Successful gemini critique
    mock_adapter._call_gemini_and_track.return_value = {
        "text": '{"score": 8, "approved": true, "critique": "Looks fine"}'
    }
    res = self_critique("roster content", "goal content", adapter=mock_adapter)
    assert res["approved"] is True
    assert res["score"] == 8

    # Scenario B: Score < 7 should auto-approve False
    mock_adapter._call_gemini_and_track.return_value = {
        "text": '{"score": 6, "approved": true, "critique": "Poor roster"}'
    }
    res = self_critique("roster content", "goal content", adapter=mock_adapter)
    assert res["approved"] is False

    # Scenario C: Exception raises in _call_gemini_and_track
    mock_adapter._call_gemini_and_track.side_effect = Exception("API connection failure")
    res = self_critique("roster content", "goal content", adapter=mock_adapter)
    assert res["approved"] is True  # fallback approval
    assert "Fallback" in res["critique"]


# =====================================================================
# 2. auth.py
# =====================================================================
@pytest.mark.anyio
async def test_auth_boost(monkeypatch):
    # default contextvar return
    assert get_current_tenant_id() == ""

    # Mock DB session
    mock_db = MagicMock()
    
    # guest access disabled by default, empty token raises 401
    monkeypatch.setattr("self_governance.auth.ALLOW_GUEST_ACCESS", False)
    with pytest.raises(HTTPException) as exc:
        await authenticate_tenant(token=None, db=mock_db)
    assert exc.value.status_code == 401
    assert "Not authenticated" in exc.value.detail

    # token not starting with tenant_ prefix or not matching database
    with pytest.raises(HTTPException) as exc:
        await authenticate_tenant(token="invalid_prefix_token", db=mock_db)
    assert exc.value.status_code == 401

    with pytest.raises(HTTPException) as exc:
        await authenticate_tenant(token="tenant_t123_key", db=mock_db)
    assert exc.value.status_code == 401


# =====================================================================
# 3. base_adapter.py
# =====================================================================
def test_base_adapter_boost():
    # Instantiate abstract class through a subclass and call methods
    class TestAdapter(BaseExecutionAdapter):
        def plan_task(self, task_description):
            return super().plan_task(task_description)
        def execute_development(self, agents, plan):
            return super().execute_development(agents, plan)
        def review_code(self, agents, changes):
            return super().review_code(agents, changes)
        def execute_tests(self, agents, changes, test_target=None):
            return super().execute_tests(agents, changes, test_target)
        def run_security_scan(self, agents, changes):
            return super().run_security_scan(agents, changes)
        def generate_documentation(self, agents, changes):
            return super().generate_documentation(agents, changes)
        def consult_advisor(self, conversation_history):
            return super().consult_advisor(conversation_history)

    adapter = TestAdapter()
    adapter.plan_task("task")
    adapter.execute_development([], {})
    adapter.review_code([], {})
    adapter.execute_tests([], {}, None)
    adapter.run_security_scan([], {})
    adapter.generate_documentation([], {})
    adapter.consult_advisor([])


# =====================================================================
# 4. cli.py
# =====================================================================
def test_cli_boost(monkeypatch, tmp_path):
    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    # Scenario A: importlib PackageNotFoundError
    import importlib.metadata
    with patch("importlib.metadata.version", side_effect=importlib.metadata.PackageNotFoundError):
        monkeypatch.setattr(sys, "argv", ["asg", "--version"])
        from self_governance.cli import parse_args
        with pytest.raises(SystemExit):
            parse_args()

    # Scenario B: Session save exceptions
    mock_session = MagicMock()
    mock_session.query.side_effect = Exception("Database error")
    monkeypatch.setattr("self_governance.cli.SessionLocal", lambda: mock_session)
    
    save_file = str(tmp_path / "asg_session.json")
    from self_governance.cli import handle_session_save
    args = MagicMock()
    args.file = save_file
    config = MagicMock()
    handle_session_save(args, config)
    assert os.path.exists(save_file)

    # Scenario C: Session restore exceptions
    from self_governance.cli import handle_session_restore
    # Missing session file
    monkeypatch.setattr(sys, "argv", ["asg", "session-restore", "--file", "nonexistent.json"])
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1

    # Exception during restore
    with open(save_file, "w") as f:
        json.dump({"pending_milestones": [{"id": 1, "name": "M1"}]}, f)

    # Raise exception in commit to escape outer try/except and rollback/exit
    mock_session.commit.side_effect = Exception("DB transaction failure")
    with pytest.raises(SystemExit) as exc:
        handle_session_restore(args, config)
    assert exc.value.code == 1

    # Run CLI as __main__ to cover line 326
    monkeypatch.setattr(sys, "argv", ["asg", "--version"])
    with pytest.raises(SystemExit):
        runpy.run_module("self_governance.cli", run_name="__main__")


# =====================================================================
# 5. consensus.py
# =====================================================================
def test_consensus_boost():
    # Requirements annealing score adjustment
    engine = ConsensusEngine(
        initial_roster=["Backend Wizard", "Security Auditor", "QA Specialist"],
        requirements=[1.0, 1.0, 1.0],
        B=1,
        target_tau=8.0,
        initial_temp=1.0,
        adapter=None
    )
    # Iteration 1 (<= B)
    score1, _ = engine._score_agent("Backend Wizard", "")
    assert score1 > 7.5

    # Run for Security Auditor and QA Specialist to cover lines 254-257
    score_sec, _ = engine._score_agent("Security Auditor", "")
    score_qa, _ = engine._score_agent("QA Specialist", "")
    assert score_sec is not None
    assert score_qa is not None

    # Decaying temperature step loop
    engine.iteration = 2
    score2, _ = engine._score_agent("Security Auditor", "")
    assert score2 is not None

    # PBFT prepare validation error
    pbft = PBFTConsensusEngine(node_id="n1", peers=["n2", "n3"], f=1)
    pbft.current_term = 2
    assert pbft.receive_prepare(sender_id="n2", term=1, index=1, message="msg") is False

    # PBFT commit validation error
    assert pbft.receive_commit(sender_id="n2", term=1, index=1, message="msg") is False


# =====================================================================
# 6. db.py
# =====================================================================
def test_db_boost(monkeypatch, tmp_path):
    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    # Non-sqlite DATABASE_URL creation - run in isolated runpy environment to avoid polluting globals
    monkeypatch.setenv("TESTING", "False")
    monkeypatch.setenv("DATABASE_URL", "postgresql://user:pass@localhost/dbname")
    with patch("sqlalchemy.create_engine") as mock_engine:
        runpy.run_path("src/self_governance/db.py")
        mock_engine.assert_called_once()

    # SovereignMemory get/set/list_keys session lifecycle (db=None)
    memory = SovereignMemory()
    memory.set(key="k1", value="val1", agent_id="agent1")
    assert memory.get("k1", "agent1") == "val1"
    assert "k1" in memory.list_keys("agent1")

    # COWMemoryBranch exception branches
    mock_db = MagicMock()
    mock_db.query.side_effect = Exception("Query failed")
    COWMemoryBranch(parent_memory=memory, db=mock_db)

    # get parent failure
    mock_parent = MagicMock()
    mock_parent.get.side_effect = Exception("Parent unavailable")
    cow_parent_fail = COWMemoryBranch(parent_memory=mock_parent, db=None)
    cow_parent_fail.fallback_storage[("agent1", "key1")] = "fallback_val"
    assert cow_parent_fail.get("key1", "agent1") == "fallback_val"

    # merge fail
    mock_parent.set.side_effect = Exception("Merge DB write failed")
    cow_merge_fail = COWMemoryBranch(parent_memory=mock_parent, db=None)
    cow_merge_fail.set("key1", "val1", "agent1")
    assert cow_merge_fail.merge() is False
    assert cow_merge_fail.fallback_storage[("agent1", "key1")] == "val1"


# =====================================================================
# 7. devserver.py
# =====================================================================
def test_devserver_boost():
    client = TestClient(dev_app)

    # metrics endpoint
    res = client.get("/metrics")
    assert res.status_code == 200

    # status endpoint
    res = client.get("/status")
    assert res.status_code == 200
    assert "runs_completed" in res.json()

    # _metric_value return 0.0 on nonexistent metric
    assert _metric_value("nonexistent_prometheus_metric") == 0.0


# =====================================================================
# 8. economics.py
# =====================================================================
def test_economics_boost():
    # empty/whitespace ast complexity
    assert analyze_ast_complexity("   \n  ") is None

    # Import From complexity matching concurrency, network, and crypto
    code_import_from = """
from asyncio import gather
from requests import get
from hashlib import md5
"""
    assert analyze_ast_complexity(code_import_from) == "gemini-2.5-pro"

    # Attribute complexity matching concurrency, network, and crypto
    code_attr = """
x = obj.threading
y = obj.socket
z = obj.ssl
"""
    assert analyze_ast_complexity(code_attr) == "gemini-2.5-pro"

    # route_model with task_type as parseable code
    assert route_model("import asyncio") == "gemini-2.5-pro"

    # route_model unknown task fallback
    assert route_model("do something general") == "gemini-1.5-pro"


# =====================================================================
# 9. gemini_adapter.py
# =====================================================================
def test_gemini_adapter_boost(monkeypatch, tmp_path):
    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    # call_safely exception block (Value/TypeError in signature inspection)
    with patch("inspect.signature", side_effect=ValueError("Signature not supported")):
        res = call_safely(lambda prompt: f"echo {prompt}", "hello", "key")
        assert res == "echo hello"

    # call_gemini_with_metadata temperature clamping & empty API key & max_output_tokens (covers line 87)
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps({
            "candidates": [{"finishReason": "STOP", "content": {"parts": [{"text": "response"}]}}],
            "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 10}
        }).encode()
        mock_urlopen.return_value.__enter__.return_value = mock_resp

        call_gemini_with_metadata("test prompt", api_key=None, temperature=3.0, max_output_tokens=100)

    # _call_gemini_and_track with return_metadata=True
    adapter = GeminiExecutionAdapter(api_key="mock_key")
    # Call real method to cover line 278
    res_track = adapter._call_gemini_and_track("prompt", return_metadata=True)
    assert isinstance(res_track, dict)

    with patch.object(adapter, "_call_gemini_and_track", return_value={"text": "raw", "finish_reason": "STOP"}):
        res = adapter._run_or_fallback("prompt", "fallback")
        assert res["output"] == "raw"

        # error path in _run_or_fallback
        adapter_err = GeminiExecutionAdapter(api_key="mock_key")
        monkeypatch.setattr(adapter_err, "_call_gemini_and_track", lambda *args, **kwargs: {"error": True})
        res_err = adapter_err._run_or_fallback("prompt", "fallback")
        assert res_err["status"] == "failed"

    # plan_task JSON load exception fallback
    adapter_plan = GeminiExecutionAdapter(api_key="mock_key")
    monkeypatch.setattr(adapter_plan, "_call_gemini_and_track", lambda *args, **kwargs: "invalid_json_text")
    plan = adapter_plan.plan_task("coding task")
    assert plan["steps"] == ["invalid_json_text"]

    # path traversal starting with package_dir
    pkg_dir = os.path.dirname(os.path.abspath(__file__))
    unsafe_path = os.path.join(pkg_dir, "unsafe.py")
    assert adapter_plan._check_path_safe(unsafe_path, os.path.abspath("."), pkg_dir) is None

    # _write_files_from_json missing written_files schema
    with pytest.raises(ValueError):
        adapter_plan._write_files_from_json('{"explanation": "none"}', ".", pkg_dir)

    # path traversal attempt in written_files list
    res_list = adapter_plan._write_files_from_json(
        json.dumps({"written_files": [{"filepath": "/etc/passwd", "content": "root"}]}),
        os.path.abspath("."), pkg_dir
    )
    assert res_list == []

    # execute_development with api_key=None
    adapter_nokey = GeminiExecutionAdapter(api_key=None)
    dev_res = adapter_nokey.execute_development([], {"task": "do"})
    assert dev_res["status"] == "completed"

    # execute_development with agents list
    adapter_key = GeminiExecutionAdapter(api_key="mock_key")
    monkeypatch.setattr(adapter_key, "_call_gemini_and_track", lambda *args, **kwargs: '{"explanation": "done", "written_files": []}')
    dev_res2 = adapter_key.execute_development([Agent(role="Wiz", prompt="pr")], {"task": "do"})
    assert dev_res2["status"] == "completed"

    # review_code subprocess run fail
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: MagicMock(returncode=1, stdout="lint issues", stderr=""))
    rev_res = adapter_nokey.review_code([], {})
    assert rev_res["status"] == "failed"

    # review_code with api_key & agents
    monkeypatch.setattr(adapter_key, "_call_gemini_and_track", lambda *args, **kwargs: "review comment")
    rev_res2 = adapter_key.review_code([Agent(role="Rev", prompt="pr")], {})
    assert rev_res2["status"] == "failed"

    # execute_tests host fallback blocked for non-testing environment
    monkeypatch.setenv("TESTING", "False")
    def mock_run_fail(cmd, *args, **kwargs):
        if "docker" in cmd:
            raise OSError("Docker missing")
        return MagicMock(returncode=0, stdout="OK", stderr="")
    monkeypatch.setattr(subprocess, "run", mock_run_fail)
    test_res = adapter_nokey.execute_tests([], {})
    assert "Host execution fallback is disabled" in test_res["output"]

    # execute_tests host fallback runs under TESTING="True" (covers lines 609-610)
    monkeypatch.setenv("TESTING", "True")
    test_res2 = adapter_nokey.execute_tests([], {}, test_target="mock_target.py")
    assert test_res2["status"] == "completed"
    
    # run_security_scan bandit fail
    monkeypatch.setattr(subprocess, "run", lambda *args, **kwargs: MagicMock(returncode=1, stdout="alert", stderr=""))
    sec_res = adapter_nokey.run_security_scan([], {})
    assert sec_res["status"] == "failed"

    # run_security_scan with api_key and agents
    sec_res2 = adapter_key.run_security_scan([Agent(role="Sec", prompt="pr")], {})
    assert sec_res2["status"] == "failed"

    # generate_documentation with api_key set (covers lines 674-676)
    monkeypatch.setattr(adapter_key, "_call_gemini_and_track", lambda *args, **kwargs: {"text": "README info"})
    doc_res = adapter_key.generate_documentation([], {})
    assert doc_res["status"] == "completed"

    # generate_documentation with api_key=None triggers _run_or_fallback line 284
    doc_res_nokey = adapter_nokey.generate_documentation([], {})
    assert doc_res_nokey["status"] == "completed"

    # consult_advisor config exception fallback
    adapter_config_fail = GeminiExecutionAdapter(api_key="mock_key")
    adapter_config_fail.config = MagicMock()
    # Define a helper function to raise exception for property
    def raise_err(*args, **kwargs):
        raise Exception("Config error")
    type(adapter_config_fail.config).advisor_max_tokens = property(raise_err)
    res_adv = adapter_config_fail.consult_advisor([])
    assert res_adv["status"] == "completed"

    # consult_advisor config is None triggers lines 690-691
    adapter_nokey.config = None
    res_adv_noconfig = adapter_nokey.consult_advisor([])
    assert res_adv_noconfig["status"] == "completed"


# =====================================================================
# 10. github_app.py
# =====================================================================
def test_github_app_boost(monkeypatch):
    client = TestClient(app)

    # health check
    res = client.get("/health")
    assert res.json() == {"status": "ok"}

    # require_admin admin_key empty and TESTING=False
    monkeypatch.setenv("TESTING", "False")
    monkeypatch.setenv("ADMIN_API_KEY", "")
    try:
        with pytest.raises(HTTPException) as exc:
            require_admin(MagicMock())
        assert exc.value.status_code == 503
    finally:
        monkeypatch.setenv("TESTING", "True")

    # require_admin invalid key
    monkeypatch.setenv("ADMIN_API_KEY", "super_secret")
    req = MagicMock()
    req.headers.get.return_value = "wrong_secret"
    with pytest.raises(HTTPException) as exc:
        require_admin(req)
    assert exc.value.status_code == 401

    # verify_signature WEBHOOK_SECRET empty
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    res_err = client.post("/webhook")
    assert res_err.status_code == 500

    # verify WEBHOOK_SECRET required check at import (run in isolated script to avoid polluting globals)
    monkeypatch.setenv("TESTING", "False")
    monkeypatch.setenv("WEBHOOK_SECRET", "")
    with pytest.raises(ValueError):
        runpy.run_path("src/self_governance/github_app.py")

    # Restore environment
    monkeypatch.setenv("TESTING", "True")
    monkeypatch.setenv("WEBHOOK_SECRET", "sec")

    # issues event action != opened
    db_mock = MagicMock()
    payload = {"action": "closed"}
    res_issues = _handle_issues_event(payload, Tenant(id="t1"), db_mock)
    assert res_issues is None

    # webhook event issues ignored (with exact body signature matching)
    payload_reopened = {"action": "reopened"}
    body_reopened = json.dumps(payload_reopened).encode("utf-8")
    sig_reopened = "sha256=" + hmac.new(b"sec", body_reopened, hashlib.sha256).hexdigest()
    res_wh = client.post("/webhook", content=body_reopened, headers={"X-GitHub-Event": "issues", "X-Hub-Signature-256": sig_reopened})
    assert res_wh.json()["status"] == "ignored"

    # trigger_succession Exception handling in issues opened event
    payload_opened = {
        "action": "opened",
        "issue": {"title": "cve security", "body": "details"}
    }
    mock_nudger = MagicMock()
    # Mock webhook_matrix to match requirement vector length 2 (covers lines 231-237)
    mock_nudger.config.webhook_matrix = [[1.0, 1.0]]
    mock_nudger.trigger_succession.side_effect = RuntimeError("Consensus failure")
    monkeypatch.setattr("self_governance.github_app.nudger", mock_nudger)

    mock_adapter_cls = MagicMock()
    mock_adapter_inst = mock_adapter_cls.return_value
    mock_adapter_inst.prompt_tokens = 50
    mock_adapter_inst.completion_tokens = 30
    monkeypatch.setattr("self_governance.gemini_adapter.GeminiExecutionAdapter", mock_adapter_cls)

    with pytest.raises(RuntimeError):
        _handle_issues_event(payload_opened, Tenant(id="t1"), db_mock)
    db_mock.add.assert_called()


# =====================================================================
# 11. learning.py
# =====================================================================
def test_learning_boost(tmp_path):
    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    # BinaryMinHeap and BinaryMaxHeap empty checks & operations
    min_heap = BinaryMinHeap()
    assert min_heap.peek() is None
    min_heap.push(5)
    min_heap.push(3)
    assert len(min_heap) == 2
    assert min_heap.peek() == 3
    assert min_heap.pop() == 3

    max_heap = BinaryMaxHeap()
    assert max_heap.peek() is None
    max_heap.push((5.0, "a"))
    max_heap.push((8.0, "b"))
    assert len(max_heap) == 2
    assert max_heap.peek() == (8.0, "b")
    assert max_heap.pop() == (8.0, "b")

    # HNSWIndex empty nodes search & bad deserialize
    hnsw = HNSWIndex()
    assert hnsw.search([1.0, 0.0]) == []
    with pytest.raises(ValueError):
        HNSWIndex.deserialize(b"BADHEADER")

    # get_encryption_key missing key check
    with patch.dict(os.environ, {}, clear=True):
        with pytest.raises(ValueError):
            get_encryption_key()

    # get_encryption_key invalid hex fallback
    with patch.dict(os.environ, {"CLAUDE_FLOW_ENCRYPTION_KEY": "Z" * 64}):
        k = get_encryption_key()
        assert len(k) == 32

    # get_encryption_key invalid base64 fallback
    with patch.dict(os.environ, {"CLAUDE_FLOW_ENCRYPTION_KEY": "invalid_base64_string"}):
        k = get_encryption_key()
        assert len(k) == 32

    # get_encryption_key valid base64 key yields 16, 24, 32 bytes (covers line 363)
    import base64
    valid_b64 = base64.b64encode(b"a" * 16).decode("utf-8")
    with patch.dict(os.environ, {"CLAUDE_FLOW_ENCRYPTION_KEY": valid_b64}):
        k = get_encryption_key()
        assert len(k) == 16

    # get_encryption_key short / long values
    with patch.dict(os.environ, {"CLAUDE_FLOW_ENCRYPTION_KEY": "short"}):
        k = get_encryption_key()
        assert len(k) == 32
    with patch.dict(os.environ, {"CLAUDE_FLOW_ENCRYPTION_KEY": "long" * 20}):
        k = get_encryption_key()
        assert len(k) == 32

    # decrypt_data corrupt payload (AESGCM decrypt exception)
    with patch.dict(os.environ, {"CLAUDE_FLOW_ENCRYPTION_KEY": "a" * 64}):
        corrupt_encrypted = b"RFE1" + os.urandom(12) + b"corrupt_ciphertext"
        with pytest.raises(ValueError):
            decrypt_data(corrupt_encrypted)

    # AgentDB insert invalid namespace & duplicate keys
    db = AgentDB()
    with pytest.raises(ValueError):
        db.insert(namespace="invalid_ns", key="k", vector=[1.0])
    
    db.insert(namespace="patterns", key="k1", vector=[1.0, 0.0])
    nid = db.insert(namespace="patterns", key="k1", vector=[0.0, 1.0])
    assert db.records["patterns"][nid].vector == [0.0, 1.0]

    # AgentDB deserialize bad header
    with pytest.raises(ValueError):
        AgentDB.deserialize(b"BADDB")

    # AgentDB load_from_file missing check
    with pytest.raises(FileNotFoundError):
        AgentDB.load_from_file("nonexistent_db_file.bin")

    # SmartRetrievalPipeline retrieve namespaces default & query dimension 0 with empty db
    empty_db = AgentDB()
    empty_pipe = SmartRetrievalPipeline(empty_db)
    res_ret = empty_pipe.retrieve(query_vector=[], namespaces=None)
    assert res_ret == []

    # SmartRetrievalPipeline retrieve mmr_lambda extremely low -> triggers best_idx == -1 break
    db.insert("patterns", "key1", [1.0, 0.0])
    res_ret2 = SmartRetrievalPipeline(db).retrieve(query_vector=[1.0, 0.0], namespaces=["patterns"], mmr_lambda=-1e12)
    assert res_ret2 == []

    # MemoryBridge exception blocks
    bridge = MemoryBridge(db, wal_path=str(tmp_path / "wal.txt"), hash_cache_path=str(tmp_path / "hashes.json"))
    
    # load hashes corruption
    with open(bridge.hash_cache_path, "w") as f:
        f.write("corrupt json")
    loaded = bridge._load_hashes()
    assert loaded == set()

    # save hashes fail
    with patch("builtins.open", side_effect=IOError("Save failed")):
        bridge._save_hashes()

    # log wal fail
    with patch("builtins.open", side_effect=IOError("WAL write failed")):
        bridge._log_wal("INSERT", "patterns", "key")

    # import file missing
    assert bridge.import_file("missing_file.txt", "patterns") is False

    # import file empty line continue
    empty_file = tmp_path / "empty_lines.txt"
    with open(empty_file, "w") as f:
        f.write("\n\nline1\n\n")
    assert bridge.import_file(str(empty_file), "patterns") is True

    # prune_file missing
    bridge.prune_file("missing_file.txt")

    # prune_file len <= max_lines
    short_file = tmp_path / "short.txt"
    with open(short_file, "w") as f:
        f.write("line1\nline2\n")
    bridge.prune_file(str(short_file), max_lines=10)
    with open(short_file, "r") as f:
        assert len(f.readlines()) == 2

    # prune_file score parsing Exception catch
    long_file = tmp_path / "long.txt"
    with open(long_file, "w") as f:
        f.write("line 1 score: abc\nline 2 confidence: 1.2.3\nline 3\nline 4\nline 5\n")
    bridge.prune_file(str(long_file), max_lines=2)


# =====================================================================
# 12. mcp.py
# =====================================================================
def test_mcp_boost():
    client = MCPClient()
    # non-existent tool
    res = client.call_tool("nonexistent", {})
    assert res["status"] == "error"

    # invalid parameter
    client.register_tool("tool1", {"properties": {"a": {"type": "string"}}}, lambda a: a)
    res2 = client.call_tool("tool1", {"b": 1})
    assert res2["status"] == "error"

    # tool implementation exception
    def error_impl():
        raise RuntimeError("Implementation crash")
    client.register_tool("tool2", {}, error_impl)
    res3 = client.call_tool("tool2", {})
    assert res3["status"] == "error"

    # refactor_and_retry_tool integer parsing regex/docs fallback
    res_ref = refactor_and_retry_tool(
        error_msg="Missing required parameter: 'val'",
        tool_name="tool",
        args={},
        docs="val: integer"
    )
    assert res_ref["args"]["val"] == 0

    # refactor_and_retry_tool missing boolean fallback
    res_ref_bool = refactor_and_retry_tool(
        error_msg="Missing required parameter: 'flag'",
        tool_name="tool",
        args={},
        docs="flag (boolean)"
    )
    assert res_ref_bool["args"]["flag"] is False

    # refactor_and_retry_tool missing integer where value is None
    res_ref_none = refactor_and_retry_tool(
        error_msg="Type mismatch for 'val': expected integer",
        tool_name="tool",
        args={"val": None},
        docs=""
    )
    assert res_ref_none["args"]["val"] == 0

    # refactor_and_retry_tool boolean type mismatches (using got float to avoid str/string match)
    res_ref2 = refactor_and_retry_tool(
        error_msg="Type mismatch for 'flag': expected boolean, got float.",
        tool_name="tool",
        args={"flag": "yes"},
        docs=""
    )
    assert res_ref2["args"]["flag"] is True

    # refactor_and_retry_tool boolean type mismatch converting to False (covers line 114)
    res_ref2_false = refactor_and_retry_tool(
        error_msg="Type mismatch for 'flag': expected boolean, got float.",
        tool_name="tool",
        args={"flag": "no"},
        docs=""
    )
    assert res_ref2_false["args"]["flag"] is False

    # refactor_and_retry_tool string type mismatch (using got float to avoid integer/int match)
    res_ref3 = refactor_and_retry_tool(
        error_msg="Type mismatch for 'text': expected string, got float.",
        tool_name="tool",
        args={"text": 123},
        docs=""
    )
    assert res_ref3["args"]["text"] == "123"

    # refactor_and_retry_tool float casting error catch
    res_ref4 = refactor_and_retry_tool(
        error_msg="Type mismatch for 'val': expected integer, got float.",
        tool_name="tool",
        args={"val": "abc"},
        docs=""
    )
    assert res_ref4["args"]["val"] == 0


# =====================================================================
# 13. memory.py
# =====================================================================
def test_memory_boost():
    # compress_context when n <= 1
    assert compress_context(["a" * 6000], max_chars=5000) == ["a" * 6000]

    # resume_state missing file
    with pytest.raises(FileNotFoundError):
        resume_state("missing_state.json")


# =====================================================================
# 14. models.py
# =====================================================================
def test_models_boost():
    config = SwarmConfig(swarm=[])
    # Delete swarm attribute
    del config["swarm"]
    # Accessing config["swarm"] raises KeyError
    with pytest.raises(KeyError):
        _ = config["swarm"]


# =====================================================================
# 15. nudger.py
# =====================================================================
def test_nudger_boost(tmp_path, monkeypatch):
    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    # _emit_event with mock working directory
    mock_wdir = MagicMock()
    _emit_event(mock_wdir, "test", {"k": "v"})

    # _emit_event with list of mocks
    _emit_event(".", "test", {"list_key": [MagicMock()]})

    # _emit_event with mock object directly as value (triggers lines 52-53)
    _emit_event(".", "test", {"mock_val": MagicMock()})

    # _emit_event nested mock key to cover line 53 of SafeJSONEncoder
    _emit_event(".", "test", {"nested_dict": {"mock_key": MagicMock()}})

    # _emit_event SafeJSONEncoder TypeError fallback
    _emit_event(".", "test", {"unserializable": object()})

    # _emit_event exception path
    with patch("builtins.open", side_effect=IOError("NDJSON write failed")):
        _emit_event(".", "test", {"k": "v"})

    # execute_hook listing directory exception
    executor = ResilientHookExecutor(str(tmp_path))
    # Create hooks folder first to bypass exists check
    os.makedirs(os.path.join(str(tmp_path), "hooks"), exist_ok=True)
    with patch("os.listdir", side_effect=OSError("Permission denied")):
        res_hook = executor.execute_hook("PreToolUse", {})
        assert res_hook["status"] == "error"

    # execute_hook command template fallback (non-py/non-sh)
    hooks_dir = tmp_path / "hooks"
    os.makedirs(hooks_dir, exist_ok=True)
    hook_file = hooks_dir / "my_hook.bin"
    with open(hook_file, "w") as f:
        f.write("")
    os.chmod(hook_file, 0o755)
    executor.execute_hook("my_hook", {})

    # execute_hook command template with .py extension (lines 101-102)
    py_hook_file = hooks_dir / "my_py_hook.py"
    with open(py_hook_file, "w") as f:
        f.write("import sys; import json; print(json.dumps({'permission': 'allow'}))")
    os.chmod(py_hook_file, 0o755)
    res_py = executor.execute_hook("my_py_hook", {})
    assert res_py["permission"] == "allow"

    # ContinuousNudger _create_dry_run_plan candidates not a list
    nudger = ContinuousNudger(working_directory=str(tmp_path))
    nudger._create_dry_run_plan({"candidates": "not_a_list"}, str(tmp_path / "dry_run.json"))

    # Write the handoff file before mocking open so os.path.exists passes
    handoff_path = tmp_path / nudger.config.handoff_file
    with open(handoff_path, "w") as f:
        f.write("status: PENDING")

    # process_handoff read exception (monkeypatch builtins.open)
    with patch("builtins.open", side_effect=IOError("Read failure")):
        nudger.process_handoff()
        assert nudger.has_transient_error is True

    # process_handoff KeyboardInterrupt read exception (line 295)
    orig_open = open
    def mock_open_ki(file, *args, **kwargs):
        if ".planning/CURRENT_STATE.md" in str(file):
            raise KeyboardInterrupt()
        return orig_open(file, *args, **kwargs)
    with patch("builtins.open", side_effect=mock_open_ki):
        with pytest.raises(KeyboardInterrupt):
            nudger.process_handoff()

    # process_handoff hook execution denies permission
    with patch.object(nudger.hook_executor, "execute_hook", return_value={"permission": "deny"}):
        with open(tmp_path / nudger.config.handoff_file, "w") as f:
            f.write("status: PENDING")
        nudger.process_handoff()

    # process_handoff execution return on failed succession (line 347)
    with open(tmp_path / nudger.config.handoff_file, "w") as f:
        f.write("status: APPROVED\ncandidates: []")
    with patch.object(nudger, "_execute_succession_safely", return_value=False):
        nudger.process_handoff()

    # process_handoff KeyboardInterrupt trigger succession exception (line 382)
    with patch.object(nudger, "trigger_succession", side_effect=KeyboardInterrupt):
        with pytest.raises(KeyboardInterrupt):
            nudger.process_handoff()

    # trigger_succession critique roster reject
    with patch("self_governance.nudger.self_critique", return_value={"approved": False, "critique": "Denied"}):
        with pytest.raises(ValueError) as exc:
            nudger.trigger_succession("status: COMPLETED\ncandidates: [\"QA Specialist\"]")
        assert "Succession roster rejected" in str(exc.value)

    # watch_handoff loop exit via too many errors (mock _stop_event.is_set to return True immediately to avoid hang)
    with patch.object(nudger._stop_event, "is_set", return_value=True):
        nudger.watch_handoff()

    # watch_handoff loop exit via KeyboardInterrupt (mock stop event is_set to raise KeyboardInterrupt only on test thread)
    nudger_ki = ContinuousNudger(working_directory=str(tmp_path))
    test_thread = threading.current_thread()
    orig_is_set = nudger_ki._stop_event.is_set
    def mock_is_set():
        if threading.current_thread() is test_thread:
            raise KeyboardInterrupt()
        # Return actual stop event status for non-test threads to avoid watchdog hangs
        return orig_is_set()
    with patch.object(nudger_ki._stop_event, "is_set", side_effect=mock_is_set):
        with pytest.raises(KeyboardInterrupt):
            nudger_ki.watch_handoff()

    # watch_handoff loop break on too many errors (lines 409-411)
    nudger_errs = ContinuousNudger(working_directory=str(tmp_path))
    nudger_errs.consecutive_transient_errors = 6
    with patch.object(nudger_errs, "process_handoff"):
        nudger_errs.watch_handoff()

    # watch_handoff transient error wait break (line 418)
    nudger_stop = ContinuousNudger(working_directory=str(tmp_path))
    nudger_stop.has_transient_error = True
    orig_wait = nudger_stop._stop_event.wait
    def mock_wait(timeout=None):
        nudger_stop._stop_event.set()
        return orig_wait(timeout)
    with patch.object(nudger_stop._stop_event, "wait", side_effect=mock_wait):
        with patch.object(nudger_stop, "process_handoff"):
            nudger_stop.watch_handoff()


# =====================================================================
# 16. p2p.py
# =====================================================================
def test_p2p_boost():
    # SwarmMarket submit_bid exceptions
    market = SwarmMarket()
    with pytest.raises(ValueError):
        market.submit_bid("nonexistent_task", "agent1", 1.0, 1.0)
    market.broadcast_task("t1", "desc", ["cap1"])
    with pytest.raises(ValueError):
        market.submit_bid("t1", "agent1", 1.0, 1.0)

    # select_winning_bid None when empty
    assert market.select_winning_bid("t1") is None

    # BoundedSet item in items return
    bset = BoundedSet(max_size=2)
    bset.add("item1")
    bset.add("item1")
    assert len(bset) == 1

    # EnhancedGossipProtocol seen msg, TTL check
    p2p_node = EnhancedGossipProtocol(node_id="n1")
    p2p_node.seen_messages.add("msg1")
    assert p2p_node.receive_gossip_enhanced("msg1", "k", "v", 1, 5) is False
    assert p2p_node.receive_gossip_enhanced("msg2", "k", "v", 1, 0) is False

    # Forward to non-enhanced peer
    plain_peer = GossipProtocol()
    p2p_node.register_peer("n2", plain_peer)
    p2p_node.receive_gossip_enhanced("msg3", "key", "val", 1, 5)
    assert plain_peer.state["key"][0] == "val"

    # publish_gossip to non-enhanced peer
    p2p_node.publish_gossip("key2", "val2", 2)
    assert plain_peer.state["key2"][0] == "val2"


# =====================================================================
# 17. security.py
# =====================================================================
def test_security_boost():
    # validate_command shlex split ValueError
    assert validate_command("'unclosed_single_quote") is False

    # validate_command empty command
    assert validate_command("  ") is True

    # pre_execution_simulation shlex split ValueError
    res = pre_execution_simulation("'unclosed")
    assert res["risk_level"] == "LOW"

    # pre_execution_simulation empty command
    res2 = pre_execution_simulation("  ")
    assert "empty command" in res2["actions"]

    # pre_execution_simulation continue on dash
    res3 = pre_execution_simulation("rm -rf file")
    assert res3["risk_level"] == "HIGH"

    # pre_execution_simulation redirections (with & without target)
    res4 = pre_execution_simulation("echo hello >")
    assert res4["risk_level"] == "MEDIUM"

    # redirection target exists (lines 93 and 97)
    res6 = pre_execution_simulation("echo hello > output.txt")
    assert "output.txt" in res6["affected_paths"]

    res7 = pre_execution_simulation("echo hello >> output.txt")
    assert "output.txt" in res7["affected_paths"]


# =====================================================================
# 18. shadow_logging.py
# =====================================================================
def test_shadow_logging_boost(tmp_path):
    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    # log_shadow_event to yaml file
    yaml_file = str(tmp_path / "shadow.yaml")
    log_shadow_event("test_event", {"a": 1}, yaml_file)
    with open(yaml_file, "r") as f:
        content = yaml.safe_load(f)
        assert content[0]["event_type"] == "test_event"

    # log_shadow_event yaml exist check (lines 27-28)
    log_shadow_event("test_event_2", {"b": 2}, yaml_file)
    with open(yaml_file, "r") as f:
        content = yaml.safe_load(f)
        assert len(content) == 2

    # log_shadow_event read exception catch
    with open(yaml_file, "w") as f:
        f.write("corrupt data: [")
    log_shadow_event("test_event_3", {"c": 3}, yaml_file)

    # log_shadow_event write exception catch
    with patch("builtins.open", side_effect=IOError("Write failed")):
        log_shadow_event("test_event_4", {"d": 4}, yaml_file)

    # check_confidence_and_prompt write exception catch
    with patch("builtins.open", side_effect=IOError("HITL write failed")):
        assert check_confidence_and_prompt(0.5, 0.7, str(tmp_path / "hitl.json")) is False


# =====================================================================
# 19. topology.py
# =====================================================================
def test_topology_boost():
    # SwarmTopology build exception
    with pytest.raises(ValueError):
        SwarmTopology(topology_type="INVALID")

    # SwarmTopology empty nodes
    top = SwarmTopology(nodes=[])
    assert top.nodes == []

    # add_node duplicate node
    top_mesh = SwarmTopology(nodes=["node1", "node2"])
    top_mesh.add_node("node1")
    assert len(top_mesh.nodes) == 2

    # add_node new node and add_edge (lines 47-49, 52-55)
    top_mesh.add_node("node3")
    assert "node3" in top_mesh.nodes
    top_mesh.add_edge("node1", "node4")
    assert "node4" in top_mesh.nodes
    assert "node4" in top_mesh.edges["node1"]

    # find_route start == end
    assert top_mesh.find_route("node1", "node1") == ["node1"]

    # find_route no path found
    top_mesh.edges["node1"].clear()
    top_mesh.edges["node2"].clear()
    assert top_mesh.find_route("node1", "node2") == []


# =====================================================================
# 20. tracing.py
# =====================================================================
def test_tracing_boost(monkeypatch):
    # Mock ConsoleSpanExporter to raise Exception - run in isolated script to avoid polluting global state
    monkeypatch.setenv("TESTING", "False")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "")
    with patch("opentelemetry.sdk.trace.export.ConsoleSpanExporter", side_effect=RuntimeError("OTel setup failure")):
        runpy.run_path("src/self_governance/tracing.py")