import os
import tempfile
from self_governance.config import OrchestratorConfig


def test_config_defaults():
    config = OrchestratorConfig()
    assert config.consensus_buffer_limit == 3
    assert config.consensus_target_threshold == 9.0
    assert config.consensus_initial_temperature == 1.0
    assert config.consensus_temperature_step == 0.1
    assert config.consensus_decay_step == 0.5
    assert config.handoff_file == "handoff.md"
    assert config.prompt_file == "prompt_draft.md"
    assert config.roster_log_file == "roster_rotation_log.md"
    assert config.default_matrix == [[1.0, 0.5], [0.0, 1.0]]


def test_config_override():
    yaml_content = """
consensus:
  buffer_limit: 5
  target_threshold: 8.0
watcher:
  handoff_file: "custom_handoff.md"
"""
    with tempfile.NamedTemporaryFile("w", suffix=".yaml", delete=False) as f:
        f.write(yaml_content)
        temp_name = f.name

    try:
        config = OrchestratorConfig(temp_name)
        assert config.consensus_buffer_limit == 5
        assert config.consensus_target_threshold == 8.0
        assert config.handoff_file == "custom_handoff.md"
        # Others remain default
        assert config.consensus_initial_temperature == 1.0
    finally:
        os.remove(temp_name)


def test_config_invalid_file():
    # Should fallback gracefully without raising
    config = OrchestratorConfig("/nonexistent/file.yaml")
    assert config.consensus_buffer_limit == 3
