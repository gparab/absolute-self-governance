"""Phase B: live TETD consensus against the real Gemini API.

Requires GEMINI_API_KEY. Emits JSON telemetry to stdout.
"""

import json
import os
import time
from self_governance.consensus import run_consensus
from self_governance.gemini_adapter import GeminiExecutionAdapter
from self_governance.billing import calculate_cost

ROSTER = ["backend_engineer", "security_auditor", "test_engineer", "doc_writer"]

adapter = GeminiExecutionAdapter(api_key=os.environ["GEMINI_API_KEY"])
start = time.time()
res = run_consensus(ROSTER, seed=42, adapter=adapter)
elapsed = time.time() - start

cost = calculate_cost(res.prompt_tokens, res.completion_tokens)
print(json.dumps({
    "roster": ROSTER,
    "approved_roster": res.approved_roster,
    "final_temperature": res.final_temperature,
    "final_threshold": res.final_threshold,
    "prompt_tokens": res.prompt_tokens,
    "completion_tokens": res.completion_tokens,
    "cost_usd": round(cost, 6),
    "wall_clock_seconds": round(elapsed, 1),
}, indent=2))
