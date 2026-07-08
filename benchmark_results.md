# Benchmark Evaluation Results

This document contains the diagnostic code challenge evaluation metrics for the Absolute Self-Governance (ASG) framework under Baseline vs ASG execution modes.

## Benchmark Execution Summary

- **Environment**: Process-isolated Sandbox (Python 3.13)
- **Model Adapter**: Gemini-2.5-Flash (Production execution using live API Key)
- **Task Coverage**: 6 diverse coding challenges.

### Evaluation Metrics Table

| Task Name | Baseline Pass | Baseline Latency | Baseline Cost | ASG Mode Pass | ASG Mode Latency | ASG Mode Cost |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Palindrome Validation** | PASS | 4.47s | $0.00021 | PASS | 10.37s | $0.00222 |
| **Memoized Fibonacci** | PASS | 5.25s | $0.00020 | PASS | 10.32s | $0.00234 |
| **Safe Division** | PASS | 4.79s | $0.00020 | PASS | 9.61s | $0.00235 |
| **String Reversal** | PASS | 4.90s | $0.00020 | PASS | 10.32s | $0.00238 |
| **Two Sum Problem** | PASS | 4.19s | $0.00023 | PASS | 10.20s | $0.00249 |
| **JSON Validator** | PASS | 4.54s | $0.00022 | PASS | 10.12s | $0.00235 |

> [!NOTE]
> The above results reflect a successful end-to-end production evaluation of both pipeline arms using live API credits.
