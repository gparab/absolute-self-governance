import os
import sys
import json
import time
import stat
import pytest
import threading

# ==========================================
# TIER 1: FEATURE COVERAGE (15 Test Cases)
# ==========================================

# --- Dimensioning Feature Coverage (5 Cases) ---

def test_dimensioning_nominal():
    from self_governance.dimensioning import dimension_swarm
    requirement_vector = [1.0, 2.0]
    transition_matrix = [[1.0, 0.5], [0.5, 1.0]]
    
    config = dimension_swarm(requirement_vector, transition_matrix)
    
    if hasattr(config, "swarm"):
        swarm = config.swarm
    else:
        swarm = config["swarm"]
        
    from collections.abc import Sequence
    assert isinstance(swarm, Sequence)
    assert len(swarm) in (4, 5)  # round([2.0, 2.5]) = [2, 2] or [2, 3]
    
    for agent in swarm:
        role = agent.role if hasattr(agent, "role") else agent["role"]
        prompt = agent.prompt if hasattr(agent, "prompt") else agent["prompt"]
        assert isinstance(role, str)
        assert isinstance(prompt, str)


def test_dimensioning_identity_matrix():
    from self_governance.dimensioning import dimension_swarm
    requirement_vector = [2.0, 3.0]
    transition_matrix = [[1.0, 0.0], [0.0, 1.0]]
    
    config = dimension_swarm(requirement_vector, transition_matrix)
    
    if hasattr(config, "swarm"):
        swarm = config.swarm
    else:
        swarm = config["swarm"]
        
    assert len(swarm) == 5
    roles = [agent.role if hasattr(agent, "role") else agent["role"] for agent in swarm]
    role_counts = {}
    for r in roles:
        role_counts[r] = role_counts.get(r, 0) + 1
    assert sorted(role_counts.values()) == [2, 3]


def test_dimensioning_role_mapping():
    from self_governance.dimensioning import dimension_swarm
    requirement_vector = [1.0, 0.0]
    transition_matrix = [[1.0, 0.0], [0.0, 1.0]]
    
    config = dimension_swarm(requirement_vector, transition_matrix)
    
    if hasattr(config, "swarm"):
        swarm = config.swarm
    else:
        swarm = config["swarm"]
        
    assert len(swarm) == 1
    role = swarm[0].role if hasattr(swarm[0], "role") else swarm[0]["role"]
    assert isinstance(role, str)


def test_dimensioning_json_schema():
    from self_governance.dimensioning import dimension_swarm
    requirement_vector = [1.0, 1.0]
    transition_matrix = [[1.0, 0.0], [0.0, 1.0]]
    
    config = dimension_swarm(requirement_vector, transition_matrix)
    
    # Try converting to dict to verify schema format
    if hasattr(config, "dict"):
        data = config.dict()
    elif hasattr(config, "model_dump"):
        data = config.model_dump()
    else:
        try:
            data = json.loads(json.dumps(config))
        except Exception:
            if hasattr(config, "swarm"):
                data = {"swarm": [{"role": a.role, "prompt": a.prompt} for a in config.swarm]}
            else:
                data = config
                
    assert isinstance(data, dict)
    assert "swarm" in data
    assert isinstance(data["swarm"], list)
    for item in data["swarm"]:
        assert isinstance(item, dict)
        assert set(item.keys()) == {"role", "prompt"}


def test_dimensioning_zero_requirements():
    from self_governance.dimensioning import dimension_swarm
    requirement_vector = [0.0, 0.0]
    transition_matrix = [[0.5, 0.5], [0.5, 0.5]]
    
    config = dimension_swarm(requirement_vector, transition_matrix)
    
    if hasattr(config, "swarm"):
        swarm = config.swarm
    else:
        swarm = config["swarm"]
        
    from collections.abc import Sequence
    assert isinstance(swarm, Sequence)
    assert len(swarm) == 0


# --- Consensus Feature Coverage (5 Cases) ---

def test_consensus_immediate_agreement():
    from self_governance.consensus import run_consensus
    initial_roster = ["agent_A", "agent_B"]
    res = run_consensus(initial_roster, B=3, target_tau=7.5, initial_temp=1.0)
    
    if hasattr(res, "approved_roster"):
        approved_roster = res.approved_roster
        final_temp = res.final_temperature
        final_threshold = res.final_threshold
    else:
        approved_roster, final_temp, final_threshold = res
        
    assert isinstance(approved_roster, list)
    assert all(isinstance(a, str) for a in approved_roster)
    assert final_temp == 1.0
    assert final_threshold == 7.5


def test_consensus_threshold_decay():
    from self_governance.consensus import run_consensus
    initial_roster = ["agent_A", "agent_B"]
    res = run_consensus(initial_roster, B=3, target_tau=9.0, delta=0.5)
    
    if hasattr(res, "approved_roster"):
        final_threshold = res.final_threshold
    else:
        _, _, final_threshold = res
        
    # If simulation requires multiple iterations, it decays
    if final_threshold < 9.0:
        assert final_threshold >= 7.0


def test_consensus_temp_scaling():
    from self_governance.consensus import run_consensus
    initial_roster = ["agent_A", "agent_B"]
    res = run_consensus(initial_roster, B=3, target_tau=9.5, initial_temp=1.0, gamma=0.1)
    
    if hasattr(res, "approved_roster"):
        final_temp = res.final_temperature
    else:
        _, final_temp, _ = res
        
    assert final_temp >= 1.0


def test_consensus_cap_threshold():
    from self_governance.consensus import run_consensus
    initial_roster = ["agent_A", "agent_B"]
    # Forces threshold decay down to cap
    res = run_consensus(initial_roster, B=10, target_tau=9.5, delta=0.5)
    
    if hasattr(res, "approved_roster"):
        final_threshold = res.final_threshold
    else:
        _, _, final_threshold = res
        
    assert final_threshold == 7.0


def test_consensus_roster_selection():
    from self_governance.consensus import run_consensus
    initial_roster = ["agent_X", "agent_Y", "agent_Z"]
    res = run_consensus(initial_roster)
    
    if hasattr(res, "approved_roster"):
        approved_roster = res.approved_roster
    else:
        approved_roster, _, _ = res
        
    assert isinstance(approved_roster, list)
    assert len(approved_roster) > 0
    assert all(isinstance(r, str) for r in approved_roster)


# --- Nudger Feature Coverage (5 Cases) ---

def test_nudger_detect_completed(tmp_path):
    from self_governance.nudger import ContinuousNudger
    
    handoff_file = tmp_path / "handoff.md"
    handoff_file.write_text("status: COMPLETED\ncandidates:\n  - agent_A\n")
    
    nudger = ContinuousNudger(working_directory=str(tmp_path))
    
    t = threading.Thread(target=nudger.watch_handoff, daemon=True)
    t.start()
    
    log_file = tmp_path / "roster_rotation_log.md"
    prompt_file = tmp_path / "prompt_draft.md"
    
    success = False
    start_time = time.time()
    while time.time() - start_time < 2.0:
        if log_file.exists() and prompt_file.exists():
            success = True
            break
        time.sleep(0.1)
        
    assert success


def test_nudger_ignore_other_status(tmp_path):
    from self_governance.nudger import ContinuousNudger
    
    handoff_file = tmp_path / "handoff.md"
    handoff_file.write_text("status: IN_PROGRESS\n")
    
    nudger = ContinuousNudger(working_directory=str(tmp_path))
    
    t = threading.Thread(target=nudger.watch_handoff, daemon=True)
    t.start()
    
    time.sleep(0.5)
    
    log_file = tmp_path / "roster_rotation_log.md"
    prompt_file = tmp_path / "prompt_draft.md"
    
    assert not log_file.exists()
    assert not prompt_file.exists()


def test_nudger_roster_log_append(tmp_path):
    from self_governance.nudger import ContinuousNudger
    nudger = ContinuousNudger(working_directory=str(tmp_path))
    
    nudger.trigger_succession("status: COMPLETED\ncandidates:\n  - agent_A\n")
    log_file = tmp_path / "roster_rotation_log.md"
    assert log_file.exists()
    content_1 = log_file.read_text()
    assert "agent_A" in content_1
    
    nudger.trigger_succession("status: COMPLETED\ncandidates:\n  - agent_B\n")
    content_2 = log_file.read_text()
    assert "agent_A" in content_2
    assert "agent_B" in content_2
    assert len(content_2) > len(content_1)


def test_nudger_prompt_draft_creation(tmp_path):
    from self_governance.nudger import ContinuousNudger
    nudger = ContinuousNudger(working_directory=str(tmp_path))
    
    nudger.trigger_succession("status: COMPLETED\ncandidates:\n  - agent_A\n")
    prompt_file = tmp_path / "prompt_draft.md"
    assert prompt_file.exists()
    assert len(prompt_file.read_text()) > 0


def test_nudger_working_directory_init(tmp_path):
    from self_governance.nudger import ContinuousNudger
    
    dir_a = tmp_path / "dir_a"
    dir_b = tmp_path / "dir_b"
    dir_a.mkdir()
    dir_b.mkdir()
    
    nudger_a = ContinuousNudger(working_directory=str(dir_a))
    nudger_b = ContinuousNudger(working_directory=str(dir_b))
    
    nudger_a.trigger_succession("status: COMPLETED\ncandidates:\n  - agent_A\n")
    nudger_b.trigger_succession("status: COMPLETED\ncandidates:\n  - agent_B\n")
    
    log_a = dir_a / "roster_rotation_log.md"
    log_b = dir_b / "roster_rotation_log.md"
    
    assert log_a.exists()
    assert log_b.exists()
    assert "agent_A" in log_a.read_text()
    assert "agent_B" in log_b.read_text()


# ==========================================
# TIER 2: BOUNDARY & CORNER CASES (15 Test Cases)
# ==========================================

# --- Dimensioning Boundary & Corner Cases (5 Cases) ---

def test_dimensioning_negative_requirements():
    from self_governance.dimensioning import dimension_swarm
    requirement_vector = [-1.5, 0.5]
    transition_matrix = [[1.0, 0.0], [0.0, 1.0]]
    
    config = dimension_swarm(requirement_vector, transition_matrix)
    
    if hasattr(config, "swarm"):
        swarm = config.swarm
    else:
        swarm = config["swarm"]
        
    from collections.abc import Sequence
    assert isinstance(swarm, Sequence)
    # Check that negative requirements clamp to 0.0, resulting in size <= 1
    assert len(swarm) <= 1


def test_dimensioning_extremely_large_requirements():
    from self_governance.dimensioning import dimension_swarm
    requirement_vector = [1000000.0]
    transition_matrix = [[10.0]]
    
    config = dimension_swarm(requirement_vector, transition_matrix)
    
    if hasattr(config, "swarm"):
        swarm = config.swarm
    else:
        swarm = config["swarm"]
        
    assert len(swarm) == 10000000


def test_dimensioning_empty_transition_matrix():
    from self_governance.dimensioning import dimension_swarm
    with pytest.raises(ValueError):
        dimension_swarm([], [])


def test_dimensioning_matrix_vector_dimension_mismatch():
    from self_governance.dimensioning import dimension_swarm
    with pytest.raises(ValueError):
        dimension_swarm([1.0, 2.0], [[1.0]])


def test_dimensioning_non_numeric_elements():
    from self_governance.dimensioning import dimension_swarm
    with pytest.raises((TypeError, ValueError)):
        dimension_swarm([1.0, "two"], [[1.0, 0.0], [0.0, 1.0]])
    with pytest.raises((TypeError, ValueError)):
        dimension_swarm([1.0, 2.0], [[1.0, "zero"], [0.0, 1.0]])


# --- Consensus Boundary & Corner Cases (5 Cases) ---

def test_consensus_zero_iterations_limit():
    from self_governance.consensus import run_consensus
    initial_roster = ["agent_A", "agent_B"]
    with pytest.raises(ValueError):
        run_consensus(initial_roster, B=0)
    with pytest.raises(ValueError):
        run_consensus(initial_roster, B=-1)


def test_consensus_empty_roster():
    from self_governance.consensus import run_consensus
    # Can raise ValueError or return empty result gracefully
    try:
        res = run_consensus([], B=3)
        if hasattr(res, "approved_roster"):
            approved_roster = res.approved_roster
        else:
            approved_roster, _, _ = res
        assert len(approved_roster) == 0
    except ValueError:
        pass


def test_consensus_target_tau_exceeds_max():
    from self_governance.consensus import run_consensus
    initial_roster = ["agent_A", "agent_B"]
    try:
        res = run_consensus(initial_roster, target_tau=15.0)
        if hasattr(res, "approved_roster"):
            final_threshold = res.final_threshold
        else:
            _, _, final_threshold = res
        assert final_threshold >= 7.0
    except ValueError:
        pass


def test_consensus_extreme_temperature():
    from self_governance.consensus import run_consensus
    initial_roster = ["agent_A", "agent_B"]
    with pytest.raises(ValueError):
        run_consensus(initial_roster, initial_temp=-1.0)
        
    res = run_consensus(initial_roster, initial_temp=1000.0)
    if hasattr(res, "approved_roster"):
        approved_roster = res.approved_roster
    else:
        approved_roster, _, _ = res
    assert isinstance(approved_roster, list)


def test_consensus_negative_gamma_delta():
    from self_governance.consensus import run_consensus
    initial_roster = ["agent_A", "agent_B"]
    with pytest.raises(ValueError):
        run_consensus(initial_roster, gamma=-0.1)
    with pytest.raises(ValueError):
        run_consensus(initial_roster, delta=-0.5)


# --- Nudger Boundary & Corner Cases (5 Cases) ---

def test_nudger_missing_handoff_file(tmp_path):
    from self_governance.nudger import ContinuousNudger
    nudger = ContinuousNudger(working_directory=str(tmp_path))
    
    t = threading.Thread(target=nudger.watch_handoff, daemon=True)
    t.start()
    
    time.sleep(0.5)
    assert t.is_alive()
    
    handoff_file = tmp_path / "handoff.md"
    handoff_file.write_text("status: COMPLETED\ncandidates:\n  - agent_A\n")
    
    log_file = tmp_path / "roster_rotation_log.md"
    success = False
    start_time = time.time()
    while time.time() - start_time < 2.0:
        if log_file.exists():
            success = True
            break
        time.sleep(0.1)
    assert success


def test_nudger_empty_handoff_file(tmp_path):
    from self_governance.nudger import ContinuousNudger
    handoff_file = tmp_path / "handoff.md"
    handoff_file.write_text("")
    
    nudger = ContinuousNudger(working_directory=str(tmp_path))
    t = threading.Thread(target=nudger.watch_handoff, daemon=True)
    t.start()
    
    time.sleep(0.5)
    log_file = tmp_path / "roster_rotation_log.md"
    assert not log_file.exists()


def test_nudger_malformed_handoff_content(tmp_path):
    from self_governance.nudger import ContinuousNudger
    handoff_file = tmp_path / "handoff.md"
    handoff_file.write_text(":::malformed:::\nthis is not YAML")
    
    nudger = ContinuousNudger(working_directory=str(tmp_path))
    t = threading.Thread(target=nudger.watch_handoff, daemon=True)
    t.start()
    
    time.sleep(0.5)
    log_file = tmp_path / "roster_rotation_log.md"
    assert not log_file.exists()


def test_nudger_roster_rotation_log_locked(tmp_path):
    from self_governance.nudger import ContinuousNudger
    nudger = ContinuousNudger(working_directory=str(tmp_path))
    
    log_file = tmp_path / "roster_rotation_log.md"
    log_file.write_text("initial log")
    os.chmod(str(log_file), stat.S_IREAD)
    
    try:
        with pytest.raises(PermissionError):
            nudger.trigger_succession("status: COMPLETED\ncandidates:\n  - agent_A\n")
    except AssertionError:
        # Handles write block gracefully
        pass
    finally:
        os.chmod(str(log_file), stat.S_IWRITE | stat.S_IREAD)


def test_nudger_concurrent_modification(tmp_path):
    from self_governance.nudger import ContinuousNudger
    handoff_file = tmp_path / "handoff.md"
    handoff_file.write_text("status: IN_PROGRESS\n")
    
    nudger = ContinuousNudger(working_directory=str(tmp_path))
    t = threading.Thread(target=nudger.watch_handoff, daemon=True)
    t.start()
    
    for i in range(10):
        handoff_file.write_text(f"status: IN_PROGRESS\niteration: {i}\n")
        time.sleep(0.01)
        
    handoff_file.write_text("status: COMPLETED\ncandidates:\n  - agent_A\n")
    
    log_file = tmp_path / "roster_rotation_log.md"
    success = False
    start_time = time.time()
    while time.time() - start_time < 2.0:
        if log_file.exists():
            success = True
            break
        time.sleep(0.1)
    assert success


# ==========================================
# TIER 3: CROSS-FEATURE COMBINATIONS (3 Test Cases)
# ==========================================

def test_cross_feature_nudger_triggers_consensus(tmp_path):
    from self_governance.nudger import ContinuousNudger
    
    handoff_file = tmp_path / "handoff.md"
    handoff_file.write_text("status: COMPLETED\ncandidates:\n  - agent_X\n  - agent_Y\n")
    
    nudger = ContinuousNudger(working_directory=str(tmp_path))
    nudger.trigger_succession(handoff_file.read_text())
    
    log_file = tmp_path / "roster_rotation_log.md"
    assert log_file.exists()
    content = log_file.read_text()
    assert "agent_X" in content or "agent_Y" in content


def test_cross_feature_consensus_feeds_dimensioning():
    from self_governance.consensus import run_consensus
    from self_governance.dimensioning import dimension_swarm
    
    initial_roster = ["agent_A", "agent_B"]
    res = run_consensus(initial_roster)
    if hasattr(res, "approved_roster"):
        approved_roster = res.approved_roster
    else:
        approved_roster, _, _ = res
        
    n = len(approved_roster)
    requirement_vector = [float(n), 1.0]
    transition_matrix = [[1.0, 0.0], [0.0, 1.0]]
    
    config = dimension_swarm(requirement_vector, transition_matrix)
    if hasattr(config, "swarm"):
        swarm = config.swarm
    else:
        swarm = config["swarm"]
        
    roles = [a.role if hasattr(a, "role") else a["role"] for a in swarm]
    role_counts = {}
    for r in roles:
        role_counts[r] = role_counts.get(r, 0) + 1
    assert sorted(role_counts.values()) in ([1, n], [n])


def test_cross_feature_full_cycle(tmp_path):
    from self_governance.nudger import ContinuousNudger
    
    handoff_file = tmp_path / "handoff.md"
    handoff_file.write_text("status: COMPLETED\ncandidates:\n  - agent_1\n  - agent_2\n")
    
    nudger = ContinuousNudger(working_directory=str(tmp_path))
    nudger.trigger_succession(handoff_file.read_text())
    
    log_file = tmp_path / "roster_rotation_log.md"
    prompt_file = tmp_path / "prompt_draft.md"
    
    assert log_file.exists()
    assert prompt_file.exists()
    assert "swarm" in prompt_file.read_text()


# ==========================================
# TIER 4: REAL-WORLD WORKLOADS (5 Test Cases)
# ==========================================

def test_workload_large_scale_succession():
    from self_governance.consensus import run_consensus
    candidates = [f"agent_{i}" for i in range(50)]
    
    start_time = time.time()
    res = run_consensus(candidates, B=5, target_tau=9.5)
    duration = time.time() - start_time
    
    if hasattr(res, "approved_roster"):
        approved_roster = res.approved_roster
    else:
        approved_roster, _, _ = res
        
    assert isinstance(approved_roster, list)
    assert duration < 1.0


def test_workload_unstable_consensus():
    from self_governance.consensus import run_consensus
    initial_roster = ["agent_A", "agent_B"]
    
    res = run_consensus(initial_roster, B=5, target_tau=9.5, initial_temp=1.0, gamma=0.1, delta=1.0)
    
    if hasattr(res, "approved_roster"):
        approved_roster = res.approved_roster
        final_temp = res.final_temperature
        final_threshold = res.final_threshold
    else:
        approved_roster, final_temp, final_threshold = res
        
    assert final_temp > 1.0
    assert final_threshold == 7.0
    assert isinstance(approved_roster, list)


def test_workload_repeated_succession_sessions(tmp_path):
    from self_governance.nudger import ContinuousNudger
    nudger = ContinuousNudger(working_directory=str(tmp_path))
    log_file = tmp_path / "roster_rotation_log.md"
    
    for i in range(5):
        nudger.trigger_succession(f"status: COMPLETED\ncandidates:\n  - agent_{i}\n")
        
    assert log_file.exists()
    content = log_file.read_text()
    for i in range(5):
        assert f"agent_{i}" in content


def test_workload_complex_dimensioning():
    from self_governance.dimensioning import dimension_swarm
    requirement_vector = [1.5, 2.0, 0.5, 3.0, 1.0]
    transition_matrix = [
        [1.0, 0.1, 0.2, 0.0, 0.1],
        [0.1, 1.0, 0.0, 0.2, 0.1],
        [0.2, 0.0, 1.0, 0.1, 0.2],
        [0.0, 0.2, 0.1, 1.0, 0.0],
        [0.1, 0.1, 0.2, 0.0, 1.0]
    ]
    
    config = dimension_swarm(requirement_vector, transition_matrix)
    
    if hasattr(config, "swarm"):
        swarm = config.swarm
    else:
        swarm = config["swarm"]
        
    from collections.abc import Sequence
    assert isinstance(swarm, Sequence)
    for agent in swarm:
        role = agent.role if hasattr(agent, "role") else agent["role"]
        prompt = agent.prompt if hasattr(agent, "prompt") else agent["prompt"]
        assert isinstance(role, str)
        assert isinstance(prompt, str)


def test_workload_recovery_on_failed_iteration(tmp_path):
    from self_governance.nudger import ContinuousNudger
    log_file = tmp_path / "roster_rotation_log.md"
    log_file.write_text("initial log entry\n")
    
    nudger = ContinuousNudger(working_directory=str(tmp_path))
    
    with pytest.raises((ValueError, TypeError, KeyError)):
        nudger.trigger_succession("status: COMPLETED\ncandidates: null")
        
    nudger_resumed = ContinuousNudger(working_directory=str(tmp_path))
    nudger_resumed.trigger_succession("status: COMPLETED\ncandidates:\n  - agent_recovered\n")
    
    assert log_file.exists()
    content = log_file.read_text()
    assert "agent_recovered" in content
    assert content.count("initial log entry") == 1
