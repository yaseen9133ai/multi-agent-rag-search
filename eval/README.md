# Evaluation suite — `eval/`

Evaluation as *code* — a set of **commands you run** that score and gate the real deployed agent
(supervisor → `kb_agent` → `search_article` over Qdrant → answer).

> **Run these by hand today; wire them into CI tomorrow.** These same commands (`make eval`,
> `make gate`, …) are designed to run unchanged in a CI pipeline (e.g. GitHub Actions) on every
> push/PR, with branch protection gating merges on `make gate`.

```
eval/
├── research_assistant/        # eval target — re-exports the REAL deployed root_agent
│   ├── __init__.py
│   └── agent.py
├── fixtures/                  # KB branch — FAST deterministic suite (free, no judge)
│   ├── test_config.json       #   delegated_and_retrieved=1.0 (custom metric, tool names only)
│   └── kb_search.test.json    #   happy path: delegate to kb_agent → search_article → cited answer
├── fixtures_web/              # WEB branch — routing check for the internet-search agent
│   ├── test_config.json       #   routed_to_web_search=1.0 (custom metric, tool names only)
│   └── web_search.test.json   #   'latest news' question → web_search_agent → exa_search
├── trajectory_metrics.py      # custom metrics: kb route, web route, retrieval-returned-context
├── fixtures_judge/            # LLM-JUDGE suite (Gemini; costs money)
│   ├── test_config.json       #   default: final_response_match_v2 only (reliable); hallucinations_v1 opt-in
│   └── report_qa.evalset.json #   4 happy-path cases (over your ingested corpus) → 8 Gemini calls/run
├── conftest.py                # path/env setup + skip guards
├── test_fast.py              # pytest: deterministic — the cheap gate you run on every change
├── test_judge.py             # pytest: LLM-judge — the costlier gate (run before merging)
├── baseline.json             # the committed "score floor"
└── compare_to_baseline.py    # the regression gate (exits non-zero on a drop — wire it into CI too)
```

> **Offline vs online.** Everything here is **offline** (fixed fixtures, before deploy). For
> **online** evaluation — scoring real *production* sessions after they happen — see
> [`../docs/gcp_deployment.md`](../docs/gcp_deployment.md) **Step 12** (a managed Langfuse evaluator;
> no local script). For the concepts behind the metrics (offline gates, online scoring, reference-based
> vs. reference-free), see [`../docs/evaluation_concepts.md`](../docs/evaluation_concepts.md).

## Prerequisites

| To run… | You need |
|---|---|
| The agent at all | `COHERE_API_KEY` (agent model) + a **running MCP Search server** reachable at `MCP_SERVER_URL` |
| The MCP server | `QDRANT_URL`/`QDRANT_API_KEY` + an ingested collection — easiest via `docker compose up -d mcp qdrant` |
| The fast suite | the above (the agent must run, calling the MCP sidecar, to produce a trajectory + response) |
| The judge suite | a `GOOGLE_API_KEY` set (the `requires_judge` skip-guard checks for it) **and** judge auth — see the note below. The LLM judge runs on **Gemini**, independent of the Cohere agent |

> **Transport:** the agent reaches the knowledge base over **Streamable HTTP** (the MCP sidecar),
> not a Stdio subprocess. So the MCP server must be up *before* you run eval. `conftest.py`'s
> `requires_mcp` guard skips the suite (rather than erroring) when nothing is listening at
> `MCP_SERVER_URL`.

> **The judge model is independent of the agent model.** Our agent is Cohere; ADK's LLM-judge
> criteria (`hallucinations_v1`, `final_response_match_v2`, …) currently run on Gemini. The
> deterministic criteria need no judge at all.
>
> **Judge auth — two requirements on the default (Vertex) path.** (1) A `GOOGLE_API_KEY` must be
> *set*, or `requires_judge` skips the suite. (2) The judge defaults to **Vertex AI** (`.env`:
> `GOOGLE_GENAI_USE_VERTEXAI=TRUE` + `GOOGLE_CLOUD_PROJECT`/`GOOGLE_CLOUD_LOCATION`), which authenticates
> with **gcloud Application Default Credentials** — so run `gcloud auth application-default login`
> (re-run when the token expires; the symptom is `RefreshError: Reauthentication is needed`). No GCP
> access? Set `GOOGLE_GENAI_USE_VERTEXAI=FALSE` to use the AI Studio key instead (mind its ~20 req/day
> cap — one judge run is ~8 Gemini calls).

**Shortcut (recommended):** from the repo root, bring the stack up, then score it:

```bash
make eval-deps        # once: creates ./.venv (uv) and installs "./agent[eval]" into it
make up               # start the whole stack (agent + mcp + qdrant + Phoenix) — tracing on
make eval             # fast deterministic suite + (separately) make gate
make eval-judge       # LLM-judge suite over the grounded evalset (needs judge auth — see below)
```

> **Why a venv (and why uv)?** The eval tooling runs on your **host** — *not* in the agent image —
> and pulls in google-adk's scoring deps. Installing those with a bare `pip install` is what bites
> people: on modern macOS/Linux it errors with `externally-managed-environment` (PEP 668), or it
> silently lands in the wrong interpreter. So `make eval-deps` installs them into a **dedicated uv
> virtualenv at `./.venv`**, and every `make eval*` target runs *that* interpreter explicitly — no
> PEP 668, no system-Python pollution, and **no `activate` step to forget**. Install uv once if
> needed: `curl -LsSf https://astral.sh/uv/install.sh | sh` (or `brew install uv`).

The explicit steps below are what the `make` targets wrap, if you'd rather run them by hand
(from the repo root):

```bash
# 1. Create the eval venv and install the [eval] extra into it (once):
uv venv .venv
uv pip install --python .venv "./agent[eval]"

# 2. Bring up the MCP Search server + Qdrant so the agent has a knowledge base to call:
docker compose up -d mcp qdrant            # serves http://localhost:3000/mcp
export MCP_SERVER_URL=http://localhost:3000/mcp

# 3. Fast, deterministic — run on every change:
.venv/bin/python -m pytest eval/test_fast.py

# 4. LLM-judge — run before merging (needs judge auth — see Prerequisites note above):
.venv/bin/python -m pytest eval/test_judge.py

# 5. The regression gate:
.venv/bin/python eval/compare_to_baseline.py            # deterministic datasets
.venv/bin/python eval/compare_to_baseline.py --judge    # + grounded report Q&A (needs judge auth — see above)
```

> **Windows (PowerShell):** the venv interpreter is `.venv\Scripts\python.exe` (not `.venv/bin/python`):
> `uv venv .venv` → `uv pip install --python .venv "./agent[eval]"` → `.venv\Scripts\python.exe -m pytest eval/test_fast.py`.
> (`make` is optional on Windows — see [`../README_powershell.md`](../README_powershell.md).)

The agent reads `MCP_SERVER_URL` (default `http://localhost:3000/mcp`) to reach the MCP Search
server over Streamable HTTP — the same env var the deployed sidecar uses. `conftest.py` defaults it
and skips the suite (`requires_mcp`) if no server is listening, so a bare `pytest` won't hard-fail.

## The test-file schema (what you're looking at)

ADK eval is driven by JSON (`EvalSet` / `EvalCase` Pydantic models). A `.test.json` is one session;
an `.evalset.json` is many. The shape that matters:

```jsonc
{
  "eval_set_id": "...",
  "eval_cases": [{
    "eval_id": "...",
    "conversation": [{                          // one entry per turn (an "invocation")
      "user_content":   { "parts": [{ "text": "..." }], "role": "user"  },
      "final_response": { "parts": [{ "text": "..." }], "role": "model" },  // the REFERENCE answer
      "intermediate_data": {
        "tool_uses": [                          // the expected TRAJECTORY (genai FunctionCall: name + args)
          { "name": "transfer_to_agent", "args": { "agent_name": "kb_agent" } },
          { "name": "search_article",    "args": { "query": "..." } }
        ],
        "intermediate_responses": []
      }
    }],
    "session_input": { "app_name": "...", "user_id": "...", "state": {} }
  }]
}
```

Note delegation to a sub-agent appears in the trajectory as a `transfer_to_agent` tool call.

## ⚠️ Why the deterministic gate uses a custom metric (not `tool_trajectory_avg_score`)

The prebuilt `tool_trajectory_avg_score` compares both the tool **name** and its **args**, and it's
all-or-nothing per turn. But our `search_article` query is generated by the LLM and changes every run
(`"non-oil GDP growth"` vs `"UAE non-oil GDP sectors 2025"`), so an exact-arg trajectory check
**can never pass reliably** for a generative agent. (Likewise `response_match_score` / ROUGE-1 rarely
clears a useful threshold against one free-form reference — generative answers vary wildly in length
and wording.)

So the fast gate scores with a small **custom metric**, `delegated_and_retrieved`
([`trajectory_metrics.py`](trajectory_metrics.py)): it asserts the stable *path* — the supervisor
delegates to `kb_agent` and `search_article` is called — by checking tool **names only**, ignoring the
volatile query. That's the "reach for the cheapest criterion that catches your bug" principle: when a
prebuilt criterion is too strict, a few lines of Python make a robust one.

> **Registration gotcha (important if you add your own metric).** `AgentEvaluator.evaluate()` reads
> `custom_metrics` from the config for the function *path*, but does **not** register the metric in
> ADK's evaluator registry — so on its own it raises `NotFoundError: <name> not found in registry`.
> `trajectory_metrics.register()` does the registration; `conftest.py` (tests) and
> `compare_to_baseline.py` (gate) call it before evaluating. Add new metric names to
> `trajectory_metrics.METRIC_NAMES` and they're covered.

Answer **quality** (grounded? correct?) is deliberately *not* checked here — it needs judgement and
lives in the judge suite ([`fixtures_judge/`](fixtures_judge), `make eval-judge`).

> Want realistic fixtures for the **judge** set? `adk web`, ask the question, then **Eval → Add
> current session**, edit, save — those criteria are semantic, so capture is about realistic content,
> not exact args.

## Three eval layers (and where groundedness lives)

Quality for a RAG agent splits into three things you can check, cheapest first:

| Layer | Question | How | Cost |
|---|---|---|---|
| **Process** | Did it take the right path — route to the right sub-agent and call its search tool? | custom metrics `delegated_and_retrieved` / `routed_to_web_search` (read `tool_uses` **names**) | free |
| **Retrieval** | Did the vector DB / web actually hand back content to ground on? | custom metric `retrieval_returned_context` (reads **`tool_responses`** — the chunks themselves) | free |
| **Answer quality** | Is the answer *faithful* to what was retrieved, and correct? | judge: `final_response_match_v2` (semantic, **default**) + `hallucinations_v1` (groundedness, **opt-in**) | Gemini |

So **groundedness has two halves**, and you don't have to leave both to the judge:
- *"Did we fetch the right stuff?"* — **deterministic**, by inspecting `tool_responses`. ADK's
  `IntermediateData.tool_responses` exposes each search tool's return value (the retrieved chunks),
  so a custom metric can assert retrieval was non-empty / contains an expected fact. See
  `retrieval_returned_context` in [`trajectory_metrics.py`](trajectory_metrics.py).
- *"Is the answer faithful to it?"* — needs judgement → `hallucinations_v1` in the judge suite.

(We keep the **hard deterministic gate** as the process metric only, because retrieval over live
Qdrant is query-sensitive — some phrasings legitimately return little — so a retrieval-content gate
can be flaky. The lab demonstrates `retrieval_returned_context` against a fixed in-memory corpus where
it's stable; here it's available to wire in when your collection is stable enough to gate on.)

## The web-search branch (`fixtures_web/`)

The supervisor's *other* specialist is `web_search_agent` (Exa). `fixtures_web/web_search.test.json`
checks that a "latest news / open web" question **routes** to it and calls `exa_search`
(`routed_to_web_search`). We assert only the **route**, not the answer text — live web content drifts
run to run, so a fixed-reference (ROUGE / `final_response_match_v2`) check would be meaningless.
Answer *faithfulness to the fetched results* is still checkable with `hallucinations_v1` (the fetched
web results are the context), but it's not in the gate. The web test makes a **live Exa call**, so
it's gated on `EXAAI_API_KEY` (`requires_exa`) and runs once — `make eval` skips it cleanly without a key.

## The grounded eval set (`fixtures_judge/report_qa.evalset.json`)

It's grounded in the **actual corpus ingested into Qdrant** — the bundled CBUAE Quarterly Economic
Report (March 2026): four happy-path questions on non-oil trade, sectoral GDP contribution, and
interest rates, with reference answers pulled directly from the pre-baked OCR text
(`ingestion/files/my_document.jsonl`) so the numbers are real, not invented. One case is asked and
answered **in Arabic**, exercising the bilingual OCR pipeline end-to-end. Kept small so routine judge
runs don't exhaust API/quota; add cases or point `JUDGE_DATASET` at a larger evalset anytime.

> **Swapping in your own document?** Rewrite these cases (question + reference answer) to match its
> real content — `final_response_match_v2` semantically compares the agent's live answer to the
> reference text here, so a stale reference means the gate fails (or worse, passes on the wrong
> grounds). Easiest way to regenerate: `adk web`, ask the question against your real corpus, then
> **Eval → Add current session** to capture the actual retrieved answer as the new reference.

It's scored by the **judge** criteria. Two exist; the default uses one:
- **`final_response_match_v2`** *(default)* — semantic match of the agent's answer to the curated
  reference, so it's robust to phrasing (unlike ROUGE or exact-arg trajectory).
- **`hallucinations_v1`** *(opt-in)* — groundedness against what the agent actually retrieved. Kept
  **out of the default run** because it fires two sequential judge calls (segment + validate) per
  response and returns `NOT_EVALUATED` (a hard fail, regardless of threshold) whenever either hiccups —
  so it makes routine runs flaky, and `num_samples` can't smooth it (the metric ignores it). Enable it
  per the opt-in note below when you want a groundedness read on paid quota.

This set scores **answers**, not trajectory — so the per-case `tool_uses[].args.query` strings are
just illustrative (the judge criteria are semantic and don't compare tool args). The deterministic
`fixtures/` suite (the `delegated_and_retrieved` custom metric) is what guards the *path* cheaply on
every change.

### What a run costs (`num_runs=1`)

**8 Gemini judge calls + ~12–20 Cohere agent calls** for the 4 cases (default config). How that arises
(verified against the installed ADK):
- **`final_response_match_v2`** samples the judge `num_samples` times per response. **It defaults to
  5** if you don't set it, so `test_config.json` pins it to **2** → 4 × 2 = 8 calls.
- **Cohere calls are the agent-under-test** (supervisor → `kb_agent` → MCP loop, ~3–5/case). They
  scale with **case count only** — metrics and `num_samples` re-score a recorded response, they never
  re-run the agent. So the only lever for Cohere/trial-quota pressure is the number of cases.

Because the default is `final_response_match_v2` only, `make eval-judge` is **reliably green**.

**Opt into groundedness** (adds `hallucinations_v1` ≈ +8 Gemini calls + the NOT_EVALUATED flakiness):
copy the block under `_groundedness_optin` in `test_config.json` into `criteria`. Best on paid quota.
Need heavier coverage generally? Add cases to the evalset (or point `JUDGE_DATASET` at your own) and
raise `num_samples`.

## The regression gate (`compare_to_baseline.py`)

`baseline.json` holds the per-criterion **score floor** for each golden dataset. The gate runs each
dataset at those thresholds and **exits non-zero if any criterion drops below its floor** — the
agent equivalent of a snapshot test. When you legitimately improve the agent, *raise* the numbers
in `baseline.json` to ratchet the floor up so it can never silently slide back.

Try the demo from the lab in production form: point `research_assistant/agent.py`'s import at a
no-retrieval variant (or comment out the `kb_agent` sub-agent), run the gate, and watch it block.

## Criteria cheat-sheet (reach for the cheapest that catches the bug)

| Criterion | Cost | Use for |
|---|---|---|
| `delegated_and_retrieved` (custom) | free | KB path / routing — **our every-change gate** (names only, robust to LLM-generated args) |
| `routed_to_web_search` (custom) | free | web path / routing — the internet-search branch (`fixtures_web/`) |
| `retrieval_returned_context` (custom) | free | did the vector DB / web hand back content (reads `tool_responses`) — deterministic half of groundedness |
| `tool_trajectory_avg_score` | free | tool path incl. **args** — great when args are stable; too strict for generated args |
| `response_match_score` (ROUGE-1) | free | answer overlap vs. reference — only useful for short/constrained answers |
| `final_response_match_v2` | Gemini | semantic answer correctness |
| `hallucinations_v1` | Gemini | **groundedness** (answer faithful to retrieved context) — opt-in; flaky (NOT_EVALUATED on a hiccup) |
| `rubric_based_*` | Gemini | quality/tool-use vs. your own rubric |
| `safety_v1`, `multi_turn_*` | Gemini + **GCP project** | safety / multi-turn conversations |
