# Benchmark Evaluation Results

This document contains the diagnostic code challenge evaluation metrics for the Absolute Self-Governance (ASG) framework under Baseline vs ASG execution modes.

## Benchmark Execution Summary

- **Environment**: Process-isolated Sandbox (Python 3.13)
- **Model Adapter**: Gemini-2.5-Flash (with fallback mock support enabled)
- **Task Coverage**: 6 diverse coding tasks covering algorithms, data structures, parsing, and math.

### Evaluation Metrics Table

| Task Name | Baseline Pass | Baseline Latency | Baseline Cost | ASG Mode Pass | ASG Mode Latency | ASG Mode Cost |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Palindrome Validation** | FAIL | 0.61s | $0.00000 | FAIL | 0.45s | $0.00000 |
| **Memoized Fibonacci** | FAIL | 0.45s | $0.00000 | FAIL | 0.49s | $0.00000 |
| **Safe Division** | FAIL | 0.48s | $0.00000 | FAIL | 0.50s | $0.00000 |
| **String Reversal** | FAIL | 0.49s | $0.00000 | FAIL | 0.54s | $0.00000 |
| **Two Sum Problem** | FAIL | 0.51s | $0.00000 | FAIL | 0.65s | $0.00000 |
| **JSON Validator** | FAIL | 0.42s | $0.00000 | FAIL | 0.51s | $0.00000 |

> [!NOTE]
> The failures and zero costs listed above reflect the mock execution fallback environment activated when running the benchmark harness without a live production `GEMINI_API_KEY` set.

## Next Steps for Benchmarking
1. **Live Production Run**: Supply a valid `GEMINI_API_KEY` to run the task challenges against the live Gemini-2.5-Flash models.
2. **Additional Iterations**: Increase repeats per task using shell loops to calculate variance and standard deviations for latency and success rate metrics.
