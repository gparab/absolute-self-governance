"""Security Validation module.

Defines validation check utilities to block forbidden shell execution commands,
restrict files write boundary paths, and simulate command execution risks.

Also provides an OWASP Top 10 + STRIDE pattern-based audit gate
(``run_security_audit``) that can be called on any string payload.
"""

import os
import re
import shlex
from dataclasses import dataclass, field
from typing import Dict, Any, List, Optional


@dataclass
class ThreatFinding:
    """A single identified threat or vulnerability finding."""

    category: str  # OWASP category or STRIDE threat type
    severity: str  # 'CRITICAL', 'HIGH', 'MEDIUM', 'LOW', 'INFO'
    description: str
    pattern_matched: str
    remediation: str


@dataclass
class SecurityAuditResult:
    """Result of a full OWASP + STRIDE security audit."""

    passed: bool
    findings: list = field(default_factory=list)  # List[ThreatFinding]
    critical_count: int = 0
    high_count: int = 0
    medium_count: int = 0
    low_count: int = 0
    audit_summary: str = ""

    def to_dict(self) -> dict:
        return {
            "passed": self.passed,
            "critical_count": self.critical_count,
            "high_count": self.high_count,
            "medium_count": self.medium_count,
            "low_count": self.low_count,
            "audit_summary": self.audit_summary,
            "findings": [
                {
                    "category": f.category,
                    "severity": f.severity,
                    "description": f.description,
                    "pattern_matched": f.pattern_matched,
                    "remediation": f.remediation,
                }
                for f in self.findings
            ],
        }


# ---------------------------------------------------------------------------
# OWASP Top 10 (2021 edition) patterns
# Each tuple: (regex_pattern, category, severity, description, remediation)
# ---------------------------------------------------------------------------
_OWASP_PATTERNS = [
    # A01: Broken Access Control
    (r"chmod\s+777", "A01:BrokenAccessControl", "CRITICAL",
     "World-writable permissions granted", "Use chmod 750 or more restrictive"),
    (r"os\.chmod\(.*0o?777", "A01:BrokenAccessControl", "CRITICAL",
     "World-writable chmod via Python", "Use 0o750 or more restrictive"),
    # A02: Cryptographic Failures
    (r"md5\s*\(", "A02:CryptographicFailure", "HIGH",
     "MD5 used for hashing (collision-vulnerable)", "Use SHA-256 or SHA-3"),
    (r"hashlib\.md5", "A02:CryptographicFailure", "HIGH",
     "hashlib.md5 usage detected", "Use hashlib.sha256 or better"),
    (r"DES|3DES|RC4|RC2", "A02:CryptographicFailure", "HIGH",
     "Weak cipher algorithm referenced", "Use AES-256-GCM"),
    # A03: Injection
    (r"eval\s*\(", "A03:Injection", "CRITICAL",
     "eval() usage — arbitrary code execution risk",
     "Use ast.literal_eval or JSON parsing"),
    (r"exec\s*\(", "A03:Injection", "CRITICAL",
     "exec() usage — arbitrary code execution risk",
     "Refactor to eliminate dynamic execution"),
    (r"subprocess.*shell\s*=\s*True", "A03:Injection", "CRITICAL",
     "shell=True in subprocess — injection risk",
     "Use shell=False with argument list"),
    (r"os\.system\s*\(", "A03:Injection", "HIGH",
     "os.system() — injection risk via unsanitized input",
     "Use subprocess.run() with shell=False"),
    # A05: Security Misconfiguration
    (r"DEBUG\s*=\s*True", "A05:Misconfiguration", "HIGH",
     "DEBUG=True in configuration", "Disable debug mode in production"),
    (r"ALLOW_ALL_ORIGINS\s*=\s*True", "A05:Misconfiguration", "HIGH",
     "CORS allow-all enabled", "Restrict CORS to known origins"),
    # A07: Identification and Authentication Failures
    (r'password\s*=\s*["\'][^"\']{0,12}["\']', "A07:AuthFailure", "HIGH",
     "Hardcoded short password detected",
     "Use environment variables for credentials"),
    (r'secret\s*=\s*["\'][^"\']{0,20}["\']', "A07:AuthFailure", "MEDIUM",
     "Hardcoded secret string detected",
     "Use environment variables or secret manager"),
    # A09: Security Logging and Monitoring Failures
    (r"except.*:\s*pass", "A09:LoggingFailure", "MEDIUM",
     "Silent exception handler — errors swallowed",
     "Add logging.error() inside except block"),
    # A10: Server-Side Request Forgery
    (r"requests\.get\(.*\+", "A10:SSRF", "MEDIUM",
     "Possible SSRF via concatenated URL",
     "Validate and allowlist URLs before fetching"),
]

# ---------------------------------------------------------------------------
# STRIDE threat patterns
# ---------------------------------------------------------------------------
_STRIDE_PATTERNS = [
    # Spoofing
    (r"skip_authentication|bypass_auth|no_auth", "STRIDE:Spoofing", "CRITICAL",
     "Authentication bypass pattern detected",
     "Enforce authentication on all endpoints"),
    # Tampering
    (r"pickle\.loads|pickle\.load", "STRIDE:Tampering", "CRITICAL",
     "pickle deserialization — arbitrary code execution via tampered data",
     "Use JSON or explicitly validated formats for deserialization"),
    # Repudiation
    (r"logging\.disable\(", "STRIDE:Repudiation", "HIGH",
     "Logging disabled — repudiation risk",
     "Never disable logging in production"),
    # Information Disclosure
    (r"traceback\.print_exc\(\)|print_exc", "STRIDE:InfoDisclosure", "MEDIUM",
     "Stack trace printed to stdout — info disclosure risk",
     "Use logger.exception() instead"),
    (r"__dict__.*json|json.*__dict__", "STRIDE:InfoDisclosure", "MEDIUM",
     "Full object dict serialized — may expose internal fields",
     "Use explicit serialization with allowlisted fields"),
    # Denial of Service
    (r"while\s+True\s*:", "STRIDE:DoS", "LOW",
     "Unbounded while True loop — potential DoS",
     "Add break condition or timeout"),
    # Elevation of Privilege
    (r"sudo|su\s+-|runAsRoot|setuid", "STRIDE:ElevationOfPrivilege", "CRITICAL",
     "Privilege escalation pattern detected",
     "Use least-privilege execution model"),
]


def run_security_audit(
    payload: str,
    fail_on_critical: bool = True,
    fail_on_high: bool = False,
) -> SecurityAuditResult:
    """Run an OWASP Top 10 + STRIDE pattern audit on a string payload.

    Args:
        payload: The string content to audit (code, YAML, prompt, config).
        fail_on_critical: If True, result.passed=False when any CRITICAL finding exists.
        fail_on_high: If True, result.passed=False when any HIGH finding exists.

    Returns:
        SecurityAuditResult with all findings and pass/fail verdict.
    """
    findings: List[ThreatFinding] = []

    all_patterns = _OWASP_PATTERNS + _STRIDE_PATTERNS
    for pattern, category, severity, description, remediation in all_patterns:
        match = re.search(pattern, payload, re.IGNORECASE)
        if match:
            findings.append(
                ThreatFinding(
                    category=category,
                    severity=severity,
                    description=description,
                    pattern_matched=match.group(0),
                    remediation=remediation,
                )
            )

    critical = sum(1 for f in findings if f.severity == "CRITICAL")
    high = sum(1 for f in findings if f.severity == "HIGH")
    medium = sum(1 for f in findings if f.severity == "MEDIUM")
    low = sum(1 for f in findings if f.severity == "LOW")

    passed = True
    if fail_on_critical and critical > 0:
        passed = False
    if fail_on_high and high > 0:
        passed = False

    summary_parts = []
    if critical > 0:
        summary_parts.append(f"{critical} CRITICAL")
    if high > 0:
        summary_parts.append(f"{high} HIGH")
    if medium > 0:
        summary_parts.append(f"{medium} MEDIUM")
    if low > 0:
        summary_parts.append(f"{low} LOW")

    summary = (
        f"Audit {'PASSED' if passed else 'FAILED'}: "
        f"{', '.join(summary_parts) if summary_parts else 'no findings'}"
    )

    return SecurityAuditResult(
        passed=passed,
        findings=findings,
        critical_count=critical,
        high_count=high,
        medium_count=medium,
        low_count=low,
        audit_summary=summary,
    )

FORBIDDEN_COMMANDS = {"curl", "wget", "sudo", "nc", "ncat", "netcat", "ping", "ssh"}
FORBIDDEN_PATTERNS = ["rm -rf /", "rm -rf /usr", "rm -rf /var", "rm -rf /etc"]


def validate_command(command: str) -> bool:
    """Checks if a command is blocked by security execution policies.

    Args:
        command: Command string to validate.

    Returns:
        True if the command passes verification.
    """
    cmd_str = command.strip()
    for pattern in FORBIDDEN_PATTERNS:
        if pattern in cmd_str:
            return False

    try:
        parts = shlex.split(cmd_str)
    except ValueError:
        return False

    if not parts:
        return True

    for part in parts:
        if part in FORBIDDEN_COMMANDS:
            return False

    return True


def validate_write_path(filepath: str, workspace_path: str, blocked_configs: Optional[List[str]] = None) -> bool:
    """Enforces write boundaries ensuring operations stay inside workspace directory.

    Blocks target filenames specified in blocked_configs configuration lists.

    Args:
        filepath: Target destination write path.
        workspace_path: Sandbox root boundary directory.
        blocked_configs: Optional list of restricted filenames.

    Returns:
        True if the path is safe to write to.
    """
    if blocked_configs is None:
        blocked_configs = ["pyproject.toml", "config.yaml", "setup.py", "uv.lock"]

    abs_workspace = os.path.abspath(workspace_path)
    abs_file = os.path.abspath(filepath)

    if not abs_file.startswith(abs_workspace):
        return False

    filename = os.path.basename(abs_file)
    if filename in blocked_configs:
        return False

    return True


def pre_execution_simulation(command: str) -> Dict[str, Any]:
    """Parses a command and runs a dry-run risk assessment of its potential impacts.

    Args:
        command: Command string to inspect.

    Returns:
        A dictionary containing risk_level (LOW, MEDIUM, HIGH), affected_paths,
        and list of actions simulated.
    """
    cmd_str = command.strip()
    try:
        parts = shlex.split(cmd_str)
    except ValueError:
        parts = cmd_str.split()

    risk_level = "LOW"
    affected_paths = []
    actions = []

    if not parts:
        return {
            "risk_level": "LOW",
            "affected_paths": [],
            "actions": ["empty command"]
        }

    base_cmd = parts[0]
    actions.append(f"Execute {base_cmd}")

    if base_cmd in {"rm", "mv", "cp", "mkdir", "rmdir", "touch"}:
        risk_level = "MEDIUM"
        for arg in parts[1:]:
            if arg.startswith("-"):
                continue
            affected_paths.append(arg)
            actions.append(f"Modify/Remove path: {arg}")
        if base_cmd == "rm" and any(arg in {"-rf", "-r", "-f"} for arg in parts):
            risk_level = "HIGH"

    elif ">" in cmd_str or ">>" in cmd_str:
        risk_level = "MEDIUM"
        actions.append("Redirection write/append")
        if ">" in parts:
            idx = parts.index(">")
            if idx + 1 < len(parts):
                affected_paths.append(parts[idx + 1])
        elif ">>" in parts:
            idx = parts.index(">>")
            if idx + 1 < len(parts):
                affected_paths.append(parts[idx + 1])

    if base_cmd in FORBIDDEN_COMMANDS or any(pattern in cmd_str for pattern in FORBIDDEN_PATTERNS):
        risk_level = "HIGH"
        actions.append("Access forbidden binary/pattern")

    for part in parts:
        if part.startswith(("/etc", "/var", "/usr", "/bin", "/sbin", "/lib")):
            risk_level = "HIGH"
            affected_paths.append(part)
            actions.append(f"Target system path: {part}")

    return {
        "risk_level": risk_level,
        "affected_paths": list(set(affected_paths)),
        "actions": actions
    }

