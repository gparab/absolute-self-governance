import os
import json
import logging
import hashlib
from unittest.mock import patch, MagicMock

from self_governance.auth import hash_key, verify_key
from self_governance.config import OrchestratorConfig
from self_governance.gemini_adapter import GeminiExecutionAdapter
from self_governance.nudger import ContinuousNudger


# ==============================================================================
# 1. API Key PBKDF2 Hashing Security & Legacy Fallback
# ==============================================================================

def test_pbkdf2_hashing_uniqueness_and_format():
    key = "tenant_t1234_secret_key"
    hash1 = hash_key(key)
    hash2 = hash_key(key)

    # 100,000 iterations, random salt -> different hashes for same key
    assert hash1 != hash2
    assert hash1.startswith("pbkdf2_sha256$100000$")
    assert hash2.startswith("pbkdf2_sha256$100000$")

    # Verify key authentication
    assert verify_key(key, hash1) is True
    assert verify_key(key, hash2) is True
    assert verify_key("tenant_t1234_wrong_key", hash1) is False


def test_legacy_sha256_fallback():
    key = "tenant_legacy_secret"
    legacy_hash = hashlib.sha256(key.encode("utf-8")).hexdigest()

    assert not legacy_hash.startswith("pbkdf2_sha256$")
    # Legacy key should authenticate correctly
    assert verify_key(key, legacy_hash) is True
    assert verify_key("wrong_legacy_key", legacy_hash) is False


# ==============================================================================
# 2. Observability of Swallowed Errors
# ==============================================================================

def test_gemini_adapter_config_initialization_failure_logs_exception(caplog):
    """
    Verify that an exception log is produced when the config file is invalid during
    GeminiExecutionAdapter initialization, rather than failing silently.
    """
    # Create an invalid YAML config file
    invalid_yaml = "consensus:\n  buffer_limit: invalid_integer_value"
    
    with caplog.at_level(logging.WARNING, logger="self_governance.gemini_adapter"):
        with patch.dict(os.environ, {"GEMINI_API_KEY": "dummy-key"}):
            # We patch open to return our invalid YAML config content
            with patch("builtins.open", patch("builtins.open", return_value=MagicMock(__enter__=lambda self: MagicMock(read=lambda: invalid_yaml)))):
                with patch("os.path.exists", return_value=True):
                    # Initializing adapter should handle the ValueError gracefully and log it with exc_info=True
                    adapter = GeminiExecutionAdapter(api_key="dummy-key", config_path="dummy_config.yaml")
                    
                    # Verify warning log is produced
                    warnings = [r for r in caplog.records if r.levelname == "WARNING"]
                    assert len(warnings) > 0
                    assert any("Failed to initialize OrchestratorConfig in GeminiExecutionAdapter constructor" in r.message for r in warnings)
                    # Verify traceback is captured (exc_info is present)
                    assert any(r.exc_info is not None for r in warnings)
                    
                    # Verify fallback configurations
                    assert adapter.model_default == "gemini-2.5-flash"


def test_nudger_dry_run_plan_parse_failure_logs_exception(tmp_path, caplog):
    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    """
    Verify that ContinuousNudger logs a warning with traceback when reading or parsing
    an invalid/malformed dry run plan file fails.
    """
    # Create the nudger working directory and a malformed plan file
    dry_run_plan_path = tmp_path / "dry_run_plan.json"
    dry_run_plan_path.write_text("invalid json content", encoding="utf-8")
    
    # Create handoff.md with APPROVED status
    handoff_path = tmp_path / ".planning/CURRENT_STATE.md"
    handoff_path.write_text("status: APPROVED\ncandidates:\n  - agent_A", encoding="utf-8")

    config = OrchestratorConfig()
    config.config_data["watcher"]["handoff_file"] = ".planning/CURRENT_STATE.md"
    nudger = ContinuousNudger(working_directory=str(tmp_path), config=config)

    with caplog.at_level(logging.WARNING, logger="self_governance.nudger"):
        with patch("self_governance.nudger.run_consensus") as mock_run_consensus:
            mock_run = MagicMock()
            mock_run.approved_roster = ["agent_A"]
            mock_run.final_temperature = 1.0
            mock_run.final_threshold = 0.9
            mock_run.cycles_needed = 1
            mock_run_consensus.return_value = mock_run
            # which must be logged with exc_info=True.
            nudger.process_handoff()

            warnings = [r for r in caplog.records if r.levelname == "WARNING"]
            assert any("Failed to read or parse dry run plan" in r.message for r in warnings)
            assert any(r.exc_info is not None for r in warnings)


def test_nudger_dry_run_plan_remove_failure_logs_exception(tmp_path, caplog):
    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    """
    Verify that ContinuousNudger logs a warning with traceback when removing
    the dry run plan file raises an exception.
    """
    # Create a valid dry run plan file
    dry_run_plan_path = tmp_path / "dry_run_plan.json"
    dry_run_plan_path.write_text(json.dumps({"status": "APPROVED"}), encoding="utf-8")
    
    # Create handoff.md with APPROVED status
    handoff_path = tmp_path / ".planning/CURRENT_STATE.md"
    handoff_path.write_text("status: APPROVED\ncandidates:\n  - agent_A", encoding="utf-8")

    config = OrchestratorConfig()
    config.config_data["watcher"]["handoff_file"] = ".planning/CURRENT_STATE.md"
    nudger = ContinuousNudger(working_directory=str(tmp_path), config=config)

    with caplog.at_level(logging.WARNING, logger="self_governance.nudger"):
        with patch("self_governance.nudger.run_consensus") as mock_run_consensus, \
             patch("os.remove", side_effect=OSError("Permission denied")):
            mock_run = MagicMock()
            mock_run.approved_roster = ["agent_A"]
            mock_run.final_temperature = 1.0
            mock_run.final_threshold = 0.9
            mock_run.cycles_needed = 1
            mock_run_consensus.return_value = mock_run

            nudger.process_handoff()

            warnings = [r for r in caplog.records if r.levelname == "WARNING"]
            assert any("Failed to remove dry run plan" in r.message for r in warnings)
            assert any(r.exc_info is not None for r in warnings)


def test_gemini_adapter_advisor_config_failure_logs_exception(caplog):
    """
    Verify that GeminiExecutionAdapter.consult_advisor logs a warning with traceback
    if retrieving advisor settings from config raises an exception.
    """
    adapter = GeminiExecutionAdapter(api_key="dummy-key")
    
    # Mock self.config to raise an exception when accessing attributes
    mock_config = MagicMock()
    type(mock_config).advisor_max_tokens = property(lambda self: exec("raise(ValueError('Simulated failure'))"))
    adapter.config = mock_config

    with caplog.at_level(logging.WARNING, logger="self_governance.gemini_adapter"):
        # Call consult_advisor which will access config attributes and raise ValueError,
        # which must be caught and logged with exc_info=True.
        # We mock _call_gemini_and_track since we only want to test the try-except config block
        with patch.object(adapter, "_call_gemini_and_track", return_value={"text": "{}"}):
            adapter.consult_advisor([])

            warnings = [r for r in caplog.records if r.levelname == "WARNING"]
            assert any("Failed to retrieve advisor settings from config" in r.message for r in warnings)
            assert any(r.exc_info is not None for r in warnings)
