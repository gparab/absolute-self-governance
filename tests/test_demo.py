import os
from self_governance.demo import run_demo, run_scenario, SCENARIOS


def test_trivial_and_complex_scenarios_produce_different_team_sizes():
    """The whole point of the demo: complex tasks staff visibly bigger teams."""
    trivial, complex_ = SCENARIOS
    trivial_result = run_scenario(trivial)
    complex_result = run_scenario(complex_)

    assert trivial_result["team_size"] >= 1
    assert complex_result["team_size"] > trivial_result["team_size"]
    assert complex_result["approved_roster"]  # consensus actually approved someone


def test_run_demo_produces_zero_cost_even_with_a_real_looking_key(monkeypatch):
    """run_consensus auto-detects GEMINI_API_KEY and would switch to a real,
    paid adapter if present — this command promises zero cost regardless of
    the ambient environment, so the guard must hold even when a key is set."""
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key-should-never-be-used")

    results = run_demo(pause_seconds=0.0)

    assert len(results) == len(SCENARIOS)
    for result in results:
        assert result["team_size"] >= 0
    # The env var must be restored exactly as the caller had it, not consumed.
    assert os.environ["GEMINI_API_KEY"] == "fake-key-should-never-be-used"


def test_run_demo_restores_env_when_key_was_absent(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    run_demo(pause_seconds=0.0)
    assert "GEMINI_API_KEY" not in os.environ


def test_demo_scenario_prompts_are_never_sent_to_a_real_adapter(monkeypatch):
    """Belt-and-suspenders: if anything in this path ever tried to construct
    a real GeminiExecutionAdapter and call out, this would fail loudly."""
    import urllib.request

    def fail_if_called(*args, **kwargs):
        raise AssertionError("demo must never make a real network call")

    monkeypatch.setattr(urllib.request, "urlopen", fail_if_called)
    monkeypatch.setenv("GEMINI_API_KEY", "fake-key")
    run_demo(pause_seconds=0.0)
