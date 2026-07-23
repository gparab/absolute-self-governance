"""Regression tests for wiring recommend_procedure/materialize_skill_card
into the real Verify Phase failure path -- previously fully built and
tested but never called from production code."""

from unittest.mock import MagicMock, patch

import pytest

from self_governance.db import Base, engine as db_engine


@pytest.fixture(autouse=True)
def _setup_db():
    Base.metadata.create_all(bind=db_engine)
    yield


def test_verify_failure_attaches_skill_card_when_procedure_recorded(tmp_path):
    from self_governance.graph_memory import GraphMemoryEngine
    from self_governance.nudger import ContinuousNudger

    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    handoff_file = tmp_path / ".planning" / "CURRENT_STATE.md"
    handoff_file.write_text("status: COMPLETED\ncandidates:\n  - agent_A\n")

    # Record a well-established procedure whose trigger closely matches the
    # failure summary text this verify failure will produce.
    engine = GraphMemoryEngine(tenant_id="default")
    for _ in range(5):
        engine.record_procedure_outcome(
            name="fix-import-error",
            trigger_pattern="pytest failed exit code import error module not found",
            steps=["Add the missing dependency to pyproject.toml", "Re-run pytest"],
            passed=True,
        )

    nudger = ContinuousNudger(working_directory=str(tmp_path))

    def fake_policed_run(name, argv, cwd, **kwargs):
        result = MagicMock()
        if name == "run_pytest":
            result.returncode = 1
            result.stdout = "pytest failed exit code import error module not found"
        else:
            result.returncode = 0
            result.stdout = ""
        return result

    with patch.object(nudger, "_policed_run", side_effect=fake_policed_run):
        with patch.object(nudger, "loop_detector"):
            nudger.process_handoff()

    written = handoff_file.read_text()
    assert "Suggested Fix (from procedural memory)" in written
    assert "fix-import-error" in written


def test_verify_failure_has_no_skill_card_when_nothing_recorded(tmp_path):
    from self_governance.nudger import ContinuousNudger

    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    handoff_file = tmp_path / ".planning" / "CURRENT_STATE.md"
    handoff_file.write_text("status: COMPLETED\ncandidates:\n  - agent_A\n")

    nudger = ContinuousNudger(working_directory=str(tmp_path))

    def fake_policed_run(name, argv, cwd, **kwargs):
        result = MagicMock()
        if name == "run_pytest":
            result.returncode = 1
            result.stdout = "some totally novel never-seen-before failure"
        else:
            result.returncode = 0
            result.stdout = ""
        return result

    with patch.object(nudger, "_policed_run", side_effect=fake_policed_run):
        with patch.object(nudger, "loop_detector"):
            nudger.process_handoff()

    written = handoff_file.read_text()
    assert "Suggested Fix (from procedural memory)" not in written


def test_verify_failure_survives_recommend_procedure_error(tmp_path):
    from self_governance.nudger import ContinuousNudger

    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    handoff_file = tmp_path / ".planning" / "CURRENT_STATE.md"
    handoff_file.write_text("status: COMPLETED\ncandidates:\n  - agent_A\n")

    nudger = ContinuousNudger(working_directory=str(tmp_path))

    def fake_policed_run(name, argv, cwd, **kwargs):
        result = MagicMock()
        result.returncode = 1 if name == "run_pytest" else 0
        result.stdout = "boom"
        return result

    with patch.object(nudger, "_policed_run", side_effect=fake_policed_run):
        with patch.object(nudger, "loop_detector"):
            with patch(
                "self_governance.nudger.GraphMemoryEngine.recommend_procedure",
                side_effect=Exception("db unavailable"),
            ):
                nudger.process_handoff()  # must not raise

    assert handoff_file.exists()
