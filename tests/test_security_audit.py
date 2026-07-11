"""Tests for OWASP + STRIDE security audit gate."""
import pytest
from self_governance.security import run_security_audit


class TestRunSecurityAudit:
    def test_clean_payload_passes(self):
        result = run_security_audit("status: COMPLETED\ncandidates:\n  - Backend Wizard")
        assert result.passed is True
        assert result.critical_count == 0
        assert "PASSED" in result.audit_summary

    def test_eval_is_critical(self):
        result = run_security_audit("x = eval(user_input)")
        assert result.critical_count >= 1
        assert result.passed is False
        categories = [f.category for f in result.findings]
        assert any("Injection" in c for c in categories)

    def test_exec_is_critical(self):
        result = run_security_audit("exec(code)")
        assert result.critical_count >= 1
        assert result.passed is False

    def test_subprocess_shell_true_is_critical(self):
        result = run_security_audit("subprocess.run(cmd, shell=True)")
        assert result.critical_count >= 1
        assert result.passed is False

    def test_md5_is_high_not_critical(self):
        result = run_security_audit("hashlib.md5(data)")
        assert result.high_count >= 1
        assert result.critical_count == 0
        # Does not fail by default (fail_on_critical only)
        assert result.passed is True

    def test_fail_on_high_flag(self):
        result = run_security_audit("hashlib.md5(data)", fail_on_high=True)
        assert result.passed is False

    def test_pickle_loads_stride_tampering(self):
        result = run_security_audit("data = pickle.loads(raw_bytes)")
        categories = [f.category for f in result.findings]
        assert any("Tampering" in c for c in categories)
        assert result.critical_count >= 1
        assert result.passed is False

    def test_silent_except_is_medium(self):
        result = run_security_audit("try:\n    risky()\nexcept Exception:\n    pass")
        assert result.medium_count >= 1

    def test_multiple_findings_accumulate(self):
        payload = """
        x = eval(user_cmd)
        hashlib.md5(data)
        except Exception: pass
        """
        result = run_security_audit(payload)
        assert len(result.findings) >= 3
        assert result.critical_count >= 1
        assert result.passed is False

    def test_to_dict_serialization(self):
        result = run_security_audit("eval(x)")
        d = result.to_dict()
        assert "passed" in d
        assert "findings" in d
        assert "critical_count" in d
        assert isinstance(d["findings"], list)
        assert len(d["findings"]) >= 1
        assert "category" in d["findings"][0]

    def test_chmod_777_is_critical(self):
        result = run_security_audit("os.chmod(path, 0o777)")
        assert result.critical_count >= 1
        assert result.passed is False

    def test_sudo_is_critical_stride(self):
        result = run_security_audit("subprocess.run(['sudo', 'rm', '-rf', '/'])")
        categories = [f.category for f in result.findings]
        assert any("ElevationOfPrivilege" in c for c in categories)
        assert result.passed is False


class TestSecurityAuditGateIntegration:
    def test_nudger_blocks_on_critical_payload(self):
        """Verify the nudger's security gate raises ValueError on critical payloads."""
        import tempfile
        import yaml
        from self_governance.nudger import ContinuousNudger

        with tempfile.TemporaryDirectory() as tmpdir:
            nudger = ContinuousNudger(working_directory=tmpdir)
            # Build a handoff with a critical security violation embedded
            payload = yaml.dump({
                "status": "COMPLETED",
                "candidates": ["Backend Wizard"],
                "notes": "run: eval(user_input)",  # triggers A03 Injection
            })
            with pytest.raises(ValueError, match="Security audit failed"):
                nudger.trigger_succession(payload)
