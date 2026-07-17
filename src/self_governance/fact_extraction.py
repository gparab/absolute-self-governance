"""Deterministic fact extraction from verify-phase tool output (Phase C2b).

Mem0-style "automatic fact extraction" (book §17.11) is normally an LLM
call over session transcripts. This repo already runs two tools whose
output *is* a list of discrete facts once parsed -- pytest's failure
list and the security auditor's findings -- so extraction here means
regex-parsing that output into individual constraint strings instead of
folding everything into one pass/fail sentence. No LLM call, no new
dependency, and it's exactly as trustworthy as the tool output it reads.
"""

import re
from typing import List

_PYTEST_FAILED_RE = re.compile(r"^FAILED\s+(.+)$")
_AUDIT_FINDING_RE = re.compile(r"\[(CRITICAL|HIGH|MEDIUM|LOW)\]\s+(.+)$")
_AUDIT_DESCRIPTION_RE = re.compile(r"Description:\s*(.+)$")


def extract_facts(pytest_output: str = "", audit_output: str = "") -> List[str]:
    """Parses pytest and security-audit CLI output into discrete facts.

    Args:
        pytest_output: Raw stdout from `pytest -q`.
        audit_output: Raw stdout from `self-governance security-audit`.

    Returns:
        One fact string per failed test and per audit finding, in the
        order encountered. Empty if neither output contains a parseable
        failure (e.g. both tools passed, or output format is unexpected).
    """
    facts: List[str] = []

    for line in pytest_output.splitlines():
        match = _PYTEST_FAILED_RE.match(line.strip())
        if match:
            facts.append(f"Test failure: {match.group(1).strip()}")

    lines = audit_output.splitlines()
    for i, line in enumerate(lines):
        match = _AUDIT_FINDING_RE.search(line)
        if not match:
            continue
        severity, category = match.group(1), match.group(2).strip()
        description = ""
        if i + 1 < len(lines):
            desc_match = _AUDIT_DESCRIPTION_RE.search(lines[i + 1])
            if desc_match:
                description = desc_match.group(1).strip()
        fact = f"Security finding [{severity}] {category}"
        if description:
            fact += f": {description}"
        facts.append(fact)

    return facts
