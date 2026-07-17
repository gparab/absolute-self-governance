from self_governance.fact_extraction import extract_facts


def test_extracts_one_fact_per_failed_test():
    pytest_output = (
        "============================= test session starts ==============================\n"
        "collected 3 items\n\n"
        "FAILED tests/test_foo.py::test_bar - AssertionError: expected 1 got 2\n"
        "FAILED tests/test_baz.py::test_qux - ValueError: bad input\n"
        "===================== 2 failed, 1 passed in 0.42s ======================\n"
    )
    facts = extract_facts(pytest_output=pytest_output)
    assert facts == [
        "Test failure: tests/test_foo.py::test_bar - AssertionError: expected 1 got 2",
        "Test failure: tests/test_baz.py::test_qux - ValueError: bad input",
    ]


def test_extracts_one_fact_per_audit_finding():
    audit_output = (
        "Security Audit Result: FAILED\n"
        "Findings:\n"
        "  \U0001f534 [CRITICAL] SQLi\n"
        "     Description: raw string interpolation in query\n"
        "     Pattern:     'SELECT * FROM'\n"
        "  \U0001f7e0 [HIGH] SSRF\n"
        "     Description: unvalidated outbound URL\n"
    )
    facts = extract_facts(audit_output=audit_output)
    assert facts == [
        "Security finding [CRITICAL] SQLi: raw string interpolation in query",
        "Security finding [HIGH] SSRF: unvalidated outbound URL",
    ]


def test_no_facts_when_both_outputs_clean():
    assert extract_facts(pytest_output="1 passed in 0.1s", audit_output="No findings.") == []


def test_empty_inputs_return_empty_list():
    assert extract_facts() == []
