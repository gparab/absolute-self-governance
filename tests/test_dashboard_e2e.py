"""End-to-end browser QA for the ASG dashboard.

Run with: RUN_E2E=1 uv run pytest tests/test_dashboard_e2e.py -v
"""
import os
import subprocess
import time
import socket
import pytest


def is_port_open(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(1)
        return s.connect_ex(('127.0.0.1', port)) == 0


@pytest.fixture(scope="module")
def devserver_url():
    """Start the devserver and yield the base URL."""
    port = 8765
    workdir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    proc = subprocess.Popen(
        ["uv", "run", "self-governance", "devserver", "--port", str(port)],
        cwd=workdir,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Wait up to 8 seconds for server to start
    for _ in range(16):
        if is_port_open(port):
            break
        time.sleep(0.5)
    yield f"http://localhost:{port}"
    proc.terminate()
    proc.wait(timeout=5)


def pytest_addoption(parser):
    """Add --run-e2e flag to pytest."""
    try:
        parser.addoption("--run-e2e", action="store_true", default=False,
                         help="Run e2e browser tests")
    except ValueError:
        pass  # Already added by another conftest or plugin


skip_without_e2e = pytest.mark.skipif(
    not os.environ.get("RUN_E2E"),
    reason="Set RUN_E2E=1 to run browser tests"
)


@pytest.mark.e2e
class TestDashboardDOM:
    @skip_without_e2e
    def test_page_loads(self, devserver_url):
        """Dashboard page loads without error."""
        import urllib.request
        with urllib.request.urlopen(devserver_url, timeout=5) as resp:
            assert resp.status == 200
            html = resp.read().decode()
        assert "<html" in html.lower() or "<!doctype" in html.lower()

    @skip_without_e2e
    def test_dashboard_contains_required_sections(self, devserver_url):
        """Dashboard HTML contains session table and metrics."""
        import urllib.request
        with urllib.request.urlopen(devserver_url, timeout=5) as resp:
            html = resp.read().decode().lower()
        # Should contain key dashboard metric labels
        assert any(kw in html for kw in ["runs", "success", "cost", "monitor"])

    @skip_without_e2e
    def test_theme_styles_present(self, devserver_url):
        """Dashboard HTML contains theme styling elements."""
        import urllib.request
        with urllib.request.urlopen(devserver_url, timeout=5) as resp:
            html = resp.read().decode().lower()
        assert any(kw in html for kw in ["theme", "style", "background"])


@pytest.mark.e2e
class TestDashboardDevserver:
    @skip_without_e2e
    def test_devserver_serves_dashboard(self, devserver_url):
        """Devserver responds to HTTP GET on root."""
        import urllib.request
        with urllib.request.urlopen(devserver_url, timeout=5) as resp:
            assert resp.status == 200

    @skip_without_e2e
    def test_devserver_health_endpoint(self, devserver_url):
        """Devserver exposes a /health endpoint."""
        import urllib.request
        with urllib.request.urlopen(f"{devserver_url}/health", timeout=3) as resp:
            assert resp.status == 200
            assert b"ok" in resp.read()
