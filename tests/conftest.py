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
    if args and isinstance(args[0], list) and args[0][0] == "uv":
        m = MagicMock()
        m.returncode = 0
        return m
    return original_run(*args, **kwargs)

@pytest.fixture(autouse=True)
def mock_subprocess_run():
    with patch("self_governance.nudger.subprocess.run", side_effect=safe_mock_run):
        yield
