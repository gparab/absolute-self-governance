import os
from unittest.mock import MagicMock, patch
import subprocess
import pytest

os.environ["TESTING"] = "True"
os.environ["ALLOW_GUEST_ACCESS"] = "true"
# Webhook signature verification is never bypassed, even under TESTING;
# tests sign their payloads with this secret instead.
os.environ["WEBHOOK_SECRET"] = "test-webhook-secret"

original_run = subprocess.run
def safe_mock_run(*args, **kwargs):
    # "docker" covers the sandboxed pytest argv nudger.py's Verify Phase now
    # uses (peer-review batch, July 2026: it used to run "uv run pytest"
    # directly on the host, matched below) -- without this, tests that
    # exercise the Verify Phase fall through to a real `docker run` call
    # against whatever Docker daemon happens to be reachable in CI/locally.
    if args and isinstance(args[0], list) and args[0][0] in ("uv", "docker"):
        m = MagicMock()
        m.returncode = 0
        m.stdout = ""
        m.stderr = ""
        return m
    return original_run(*args, **kwargs)

@pytest.fixture(autouse=True)
def mock_subprocess_run():
    with patch("self_governance.nudger.subprocess.run", side_effect=safe_mock_run):
        yield
