import os
import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch, MagicMock


from self_governance.auth import hash_key, verify_key
from self_governance.config import OrchestratorConfig
from self_governance.gemini_adapter import GeminiExecutionAdapter
from self_governance.nudger import ContinuousNudger

# ==============================================================================
# 1. PBKDF2 Cryptographic Security & Robustness under Load
# ==============================================================================

def test_pbkdf2_salt_entropy_and_uniqueness():
    """Verify that salt generation is cryptographically secure and unique."""
    hashes = [hash_key("test_key") for _ in range(100)]
    salts = []
    for h in hashes:
        parts = h.split("$")
        assert len(parts) == 4
        assert parts[0] == "pbkdf2_sha256"
        assert parts[1] == "100000"
        salt = parts[2]
        # Check that salt is 16 hex characters (8 bytes of entropy)
        assert len(salt) == 16
        # Salt should only contain valid hex characters
        int(salt, 16)
        salts.append(salt)
    
    # All salts must be completely unique
    assert len(set(salts)) == 100


def test_pbkdf2_verification_robustness():
    """Test verification against malformed or hostile inputs to ensure no crashes or auth bypasses."""
    key = "tenant_test_key"
    correct_hash = hash_key(key)

    # Malformed hashes should return False and not raise exceptions
    malformed_hashes = [
        "",
        "pbkdf2_sha256$",
        "pbkdf2_sha256$100000",
        "pbkdf2_sha256$100000$salt",
        "pbkdf2_sha256$invalid_iter$salt$hash",
        "pbkdf2_sha256$100000$salt$hash$extra",
        "pbkdf2_sha256$-100$salt$hash", # Negative iterations
        "pbkdf2_sha256$0$salt$hash",    # Zero iterations
        "invalid_prefix$100000$salt$hash",
        "a" * 1000, # Large string
    ]

    for bh in malformed_hashes:
        assert verify_key(key, bh) is False

    # Test key values of unusual length or type
    assert verify_key("", correct_hash) is False
    assert verify_key("a" * 10000, correct_hash) is False


def test_pbkdf2_performance_and_concurrency():
    """Verify performance characteristics under load to ensure PBKDF2 hashing works concurrently without lockup."""
    key = "tenant_perf_key"
    hashed = hash_key(key)

    # We do a concurrent verification load test
    start_time = time.time()
    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [executor.submit(verify_key, key, hashed) for _ in range(50)]
        results = [f.result() for f in futures]
    
    elapsed = time.time() - start_time
    assert all(results)
    
    # 50 verifications on 10 threads should complete in a reasonable time,
    # proving no global interpreter locks are blocking pbkdf2_hmac (which releases the GIL in CPython).
    print(f"Concurrent PBKDF2 verification took {elapsed:.2f} seconds for 50 ops.")
    assert elapsed < 5.0  # Safe threshold for local test runs


# ==============================================================================
# 2. Swallowed Errors Observability & Handling
# ==============================================================================

def test_gemini_adapter_config_parse_failure_observability(caplog):
    """Ensure that failed OrchestratorConfig initialization logs a traceback."""
    invalid_yaml = "invalid_yaml: [missing bracket"
    
    with caplog.at_level(logging.WARNING, logger="self_governance.gemini_adapter"):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "dummy-key"}):
            with patch("builtins.open", MagicMock(return_value=MagicMock(__enter__=lambda s: MagicMock(read=lambda: invalid_yaml)))):
                with patch("os.path.exists", return_value=True):
                    adapter = GeminiExecutionAdapter(api_key="dummy-key", config_path="config.yaml")
                    assert adapter is not None

                    
                    # Verify a warning was logged with traceback (exc_info)
                    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
                    assert len(warnings) > 0
                    assert any("Failed to initialize OrchestratorConfig" in r.message for r in warnings)
                    assert any(r.exc_info is not None for r in warnings)


def test_nudger_dry_run_permission_denied_observability(tmp_path, caplog):
    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    """Ensure that os.remove failure logs the exact exception stack trace."""
    dry_run_plan_path = tmp_path / "dry_run_plan.json"
    dry_run_plan_path.write_text(json.dumps({"status": "APPROVED"}), encoding="utf-8")
    
    handoff_path = tmp_path / ".planning/CURRENT_STATE.md"
    handoff_path.write_text("status: APPROVED\ncandidates:\n  - agent_X", encoding="utf-8")

    config = OrchestratorConfig()
    config.config_data["watcher"]["handoff_file"] = ".planning/CURRENT_STATE.md"
    nudger = ContinuousNudger(working_directory=str(tmp_path), config=config)

    with caplog.at_level(logging.WARNING, logger="self_governance.nudger"):
        with patch("self_governance.nudger.run_consensus") as mock_run_consensus, \
             patch("os.remove", side_effect=PermissionError("Mock Permission Denied")):
            
            mock_run = MagicMock()
            mock_run.approved_roster = ["agent_X"]
            mock_run.final_temperature = 1.0
            mock_run.final_threshold = 0.9
            mock_run.cycles_needed = 1
            mock_run_consensus.return_value = mock_run

            nudger.process_handoff()

            warnings = [r for r in caplog.records if r.levelname == "WARNING"]
            assert any("Failed to remove dry run plan: Mock Permission Denied" in r.message for r in warnings)
            assert any(r.exc_info is not None for r in warnings)
