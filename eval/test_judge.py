"""LLM-as-judge eval — the costlier gate; run it before you merge (it costs money + latency).

(Wire this exact command into CI — e.g. GitHub Actions — to run it on PRs / nightly.)

Scores an eval set over whatever corpus is ingested into Qdrant, using a **Gemini**
judge that's independent of the Cohere agent under test (so it needs GOOGLE_API_KEY; the test skips
cleanly without one). Two LLM-as-judge criteria exist:

  • `final_response_match_v2`  → semantic match of the answer to the curated reference (phrasing-robust)
  • `hallucinations_v1`        → groundedness: are the answer's claims supported by retrieved context?

── One set, one metric by default (per run, num_runs=1) ──────────────────────────────────────────────
`fixtures_judge/report_qa.evalset.json` — 4 happy-path cases scored by `final_response_match_v2` ONLY
→ 8 Gemini judge calls + ~12-20 Cohere agent calls. Deliberately small AND single-metric so routine
runs are RELIABLY GREEN. Override the dataset with `make eval-judge JUDGE_DATASET=...`.

Why not `hallucinations_v1` by default? It fires two sequential judge calls/response (segment+validate)
and returns NOT_EVALUATED (None) whenever EITHER hiccups — which ADK treats as a HARD FAIL regardless
of threshold, and num_samples can't smooth it (the metric ignores num_samples in this ADK version). To
opt into groundedness on paid quota, copy the `_groundedness_optin` block in test_config.json into
`criteria` (adds ~8 Gemini calls + that flakiness).

Call-count notes (verified against the installed ADK): final_response_match_v2 samples num_samples
times/response (DEFAULTS TO 5 when unset — test_config.json pins it to 2). Cohere (agent-under-test)
calls scale with CASE COUNT only — num_samples/metrics re-score a recorded response, they never re-run
the agent. The MERGE GATE (`make gate-judge` / baseline.json) also scores final_response_match_v2 only.

The judge runs on **Vertex AI** (deploy/.env: GOOGLE_GENAI_USE_VERTEXAI=TRUE + GOOGLE_CLOUD_PROJECT/
LOCATION, auth via `gcloud auth application-default login`), billed to GCP / free-trial credits — this
sidesteps the AI-Studio free tier's ~20-requests-PER-DAY cap. To fall back to AI Studio, set
GOOGLE_GENAI_USE_VERTEXAI=FALSE in .env (mind that daily cap).
"""
import os

import pytest
from google.adk.evaluation.agent_evaluator import AgentEvaluator

from conftest import FIXTURES_JUDGE, requires_cohere, requires_judge, requires_mcp

# The judge eval set (fixtures_judge/report_qa.evalset.json; config there scores final_response_match_v2
# by default — see its _groundedness_optin to add hallucinations_v1). Override with JUDGE_DATASET.
_JUDGE_DATASET = os.environ.get("JUDGE_DATASET", "report_qa.evalset.json")


@requires_cohere
@requires_mcp
@requires_judge
@pytest.mark.asyncio
async def test_report_qa_groundedness_and_correctness():
    await AgentEvaluator.evaluate(
        agent_module="research_assistant",
        eval_dataset_file_path_or_dir=f"{FIXTURES_JUDGE}/{_JUDGE_DATASET}",
        num_runs=1,  # judge calls cost money; one run over the dataset is plenty for the gate
    )
