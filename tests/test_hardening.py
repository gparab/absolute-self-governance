"""Tests for the production-hardening guards added in the 10/10 push."""

import importlib
import json
import urllib.error

import pytest

from self_governance.consensus import run_consensus
from self_governance.gemini_adapter import (
    GeminiExecutionAdapter,
    call_gemini_with_metadata,
)


def test_prompt_size_cap():
    with pytest.raises(ValueError, match="exceeds the 500,000-character limit"):
        call_gemini_with_metadata("x" * 500_001, "key")


def test_api_failure_returns_error_channel(monkeypatch):
    def boom(*a, **kw):
        raise urllib.error.URLError("down")

    monkeypatch.setattr("urllib.request.urlopen", boom)
    monkeypatch.setattr("time.sleep", lambda s: None)
    res = call_gemini_with_metadata("hi", "key")
    assert res["error"] is True
    assert res["finish_reason"] == "ERROR"
    assert res["text"] == ""


def test_run_or_fallback_reports_failure(monkeypatch):
    adapter = GeminiExecutionAdapter(api_key="key")
    monkeypatch.setattr(
        adapter, "_call_gemini_and_track",
        lambda *a, **kw: {"text": "", "error": True},
    )
    res = adapter._run_or_fallback("prompt", "fallback")
    assert res["status"] == "failed"


def test_roster_cap_applies_only_with_adapter():
    big = [f"agent_{i}" for i in range(101)]
    with pytest.raises(ValueError, match="maximum size of 100"):
        run_consensus(big, adapter=GeminiExecutionAdapter(api_key="key"))
    # Mock (adapter-less) runs stay uncapped
    res = run_consensus(big[:150], seed=1)
    assert res.approved_roster


def test_consensus_wall_clock_budget():
    # Unreachable threshold forces looping; zero budget exits best-effort
    # after the first full iteration instead of grinding to the 1000 cap.
    res = run_consensus(
        ["agent_A", "agent_B"], B=1, target_tau=20.0, seed=7, max_seconds=0.0
    )
    assert res.approved_roster  # best-effort result, not an exception


def _fake_track(text):
    def fake(*a, return_metadata=False, **kw):
        if return_metadata:
            return {"text": text, "finish_reason": "STOP"}
        return text

    return fake


def test_failed_api_call_scores_as_rejection(monkeypatch):
    adapter = GeminiExecutionAdapter(api_key="key")
    monkeypatch.setattr(adapter, "_call_gemini_and_track", _fake_track(""))
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    res = run_consensus(["agent_A"], B=1, target_tau=9.0, seed=1, adapter=adapter)
    # All votes fail -> tau decays to floor 7.0 but scores are 1.0: no approval
    # until the iteration/deadline caps produce the best-effort roster.
    assert res.final_threshold == 7.0


def test_score_clamping(monkeypatch):
    adapter = GeminiExecutionAdapter(api_key="key")
    monkeypatch.setattr(
        adapter, "_call_gemini_and_track",
        _fake_track(json.dumps({"score": 999.0, "reason": "r"})),
    )
    monkeypatch.setenv("GEMINI_API_KEY", "key")
    res = run_consensus(["agent_A"], B=1, target_tau=9.0, seed=1, adapter=adapter)
    assert res.final_threshold == 7.0  # NaN never counted as approval


def test_config_rejects_unknown_top_level_key(tmp_path):
    from self_governance.config import OrchestratorConfig

    bad = tmp_path / "c.yaml"
    bad.write_text("consenssus:\n  buffer_limit: 3\n")
    with pytest.raises(ValueError, match="Unknown config keys"):
        OrchestratorConfig(str(bad))


def test_config_rejects_type_mismatch(tmp_path):
    from self_governance.config import OrchestratorConfig

    bad = tmp_path / "c.yaml"
    bad.write_text('consensus:\n  buffer_limit: "hello"\n')
    with pytest.raises(ValueError, match="must be"):
        OrchestratorConfig(str(bad))


def test_config_rejects_non_mapping_section(tmp_path):
    from self_governance.config import OrchestratorConfig

    bad = tmp_path / "c.yaml"
    bad.write_text("consensus: [1, 2]\n")
    with pytest.raises(ValueError, match="must be a mapping"):
        OrchestratorConfig(str(bad))


def test_config_allows_new_keys_in_known_sections(tmp_path):
    from self_governance.config import OrchestratorConfig

    ok = tmp_path / "c.yaml"
    ok.write_text("consensus:\n  buffer_limit: 5\n  future_knob: 1\n")
    cfg = OrchestratorConfig(str(ok))
    assert cfg.consensus_buffer_limit == 5


def test_tracing_exporter_selection(monkeypatch):
    import self_governance.tracing as tracing

    monkeypatch.setenv("TESTING", "False")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", "http://localhost:4318")
    importlib.reload(tracing)
    assert tracing.tracer is not None
    monkeypatch.setenv("TESTING", "True")
    importlib.reload(tracing)


def test_cli_stats_watch(monkeypatch, capsys):
    from self_governance import cli

    monkeypatch.setattr("sys.argv", ["self-governance", "stats", "--watch"])
    monkeypatch.setattr(
        "time.sleep", lambda s: (_ for _ in ()).throw(KeyboardInterrupt)
    )
    cli.main()
    assert "Dashboard" in capsys.readouterr().out


def test_cli_dev_mode(monkeypatch, tmp_path, capsys):
    import uvicorn
    from self_governance import cli
    from self_governance.nudger import ContinuousNudger

    monkeypatch.setattr(
        "sys.argv",
        ["self-governance", "dev", "--workdir", str(tmp_path), "--port", "18999"],
    )
    monkeypatch.setattr(uvicorn.Server, "run", lambda self: None)
    monkeypatch.setattr(
        ContinuousNudger,
        "watch_handoff",
        lambda self: (_ for _ in ()).throw(KeyboardInterrupt),
    )
    cli.main()
    out = capsys.readouterr().out
    assert "ASG dev mode" in out
    assert "18999" in out


def test_gemini_key_not_in_url(monkeypatch):
    captured = {}

    class FakeResponse:
        def read(self):
            return b'{"candidates": [], "usageMetadata": {}}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    call_gemini_with_metadata("hi", "sekret-key")
    assert "sekret-key" not in captured["url"]
    assert captured["headers"].get("X-goog-api-key") == "sekret-key"
