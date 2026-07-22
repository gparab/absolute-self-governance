"""Regression tests for the crash/daemon-failure fixes from the July 2026
peer-review batch: the `retro --as-json` CLI crash, GitMutationRateLimitRule's
lifetime-vs-rolling-window cap, and unhandled Ship Phase merge conflicts."""

from unittest.mock import MagicMock, patch

from self_governance.cli import parse_args, handle_retro
from self_governance.policy import PolicyAction
from self_governance.policy_rules.rate_limits import GitMutationRateLimitRule


# --- #8: `retro` CLI crash on missing --as-json -----------------------------

def test_retro_parser_accepts_as_json_flag(monkeypatch):
    monkeypatch.setattr("sys.argv", ["self-governance", "retro", "--as-json"])
    args = parse_args()
    assert args.as_json is True


def test_retro_parser_defaults_as_json_false(monkeypatch):
    monkeypatch.setattr("sys.argv", ["self-governance", "retro"])
    args = parse_args()
    assert args.as_json is False


def test_handle_retro_as_json_does_not_raise(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["self-governance", "retro", "--as-json"])
    args = parse_args()
    with patch("self_governance.learning.get_learning_state", return_value={"runs_completed": 1}):
        handle_retro(args)
    out = capsys.readouterr().out
    assert "runs_completed" in out


def test_handle_retro_plain_does_not_raise(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["self-governance", "retro"])
    args = parse_args()
    with patch("self_governance.learning.get_learning_state", return_value={"runs_completed": 1}):
        handle_retro(args)
    out = capsys.readouterr().out
    assert out  # some report text was printed, no AttributeError


# --- #10: GitMutationRateLimitRule rolling window, not lifetime cap ---------

def test_git_mutation_rate_limit_recovers_after_window_elapses():
    fake_time = [0.0]
    rule = GitMutationRateLimitRule(max_mutations=1, window_seconds=10.0, time_fn=lambda: fake_time[0])
    action = PolicyAction(name="git_commit", argv=["git", "commit", "-m", "x"])

    assert rule.evaluate(action) is None  # 1st allowed
    decision = rule.evaluate(action)
    assert decision is not None  # 2nd denied, still within window

    fake_time[0] = 11.0  # advance past the window
    assert rule.evaluate(action) is None  # capacity recovered, no permanent lockup


def test_git_mutation_rate_limit_denies_burst_within_window():
    fake_time = [100.0]
    rule = GitMutationRateLimitRule(max_mutations=2, window_seconds=3600.0, time_fn=lambda: fake_time[0])
    action = PolicyAction(name="git_commit", argv=["git", "commit", "-m", "x"])

    assert rule.evaluate(action) is None
    assert rule.evaluate(action) is None
    decision = rule.evaluate(action)
    assert decision is not None


# --- #11: unhandled Ship Phase merge conflicts ------------------------------

def test_ship_phase_aborts_merge_and_preserves_worktree_on_conflict(tmp_path):
    from self_governance.nudger import ContinuousNudger

    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    handoff_file = tmp_path / ".planning" / "CURRENT_STATE.md"
    handoff_file.write_text("status: COMPLETED\ncandidates:\n  - agent_A\n")

    nudger = ContinuousNudger(working_directory=str(tmp_path))
    worktree_path = tmp_path / ".planning" / "worktrees" / "active_task"
    worktree_path.mkdir(parents=True)

    calls = []

    def fake_policed_run(name, argv, cwd, **kwargs):
        calls.append(name)
        result = MagicMock()
        if name == "git_merge":
            result.returncode = 1
            result.stdout = "CONFLICT (content): Merge conflict in x.py"
        else:
            result.returncode = 0
            result.stdout = ""
        return result

    with patch.object(nudger, "_policed_run", side_effect=fake_policed_run):
        with patch.object(nudger, "loop_detector"):
            nudger.process_handoff()

    assert "git_merge" in calls
    assert "git_merge_abort" in calls
    assert "git_worktree_remove" not in calls
    assert "git_branch_delete_scratch" not in calls
    assert worktree_path.exists()  # preserved for manual resolution


def test_ship_phase_removes_worktree_on_clean_merge(tmp_path):
    from self_governance.nudger import ContinuousNudger

    (tmp_path / ".planning").mkdir(parents=True, exist_ok=True)
    handoff_file = tmp_path / ".planning" / "CURRENT_STATE.md"
    handoff_file.write_text("status: COMPLETED\ncandidates:\n  - agent_A\n")

    nudger = ContinuousNudger(working_directory=str(tmp_path))
    worktree_path = tmp_path / ".planning" / "worktrees" / "active_task"
    worktree_path.mkdir(parents=True)

    calls = []

    def fake_policed_run(name, argv, cwd, **kwargs):
        calls.append(name)
        result = MagicMock()
        result.returncode = 0
        result.stdout = ""
        return result

    with patch.object(nudger, "_policed_run", side_effect=fake_policed_run):
        with patch.object(nudger, "loop_detector"):
            nudger.process_handoff()

    assert "git_merge" in calls
    assert "git_merge_abort" not in calls
    assert "git_worktree_remove" in calls
    assert "git_branch_delete_scratch" in calls
