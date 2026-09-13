# Grokking agent evaluation: offline gates, online scoring & the metrics

A short, plain-English reference for the evaluation vocabulary used in this project. Read it once
and the metrics in `eval/` (and the Langfuse evaluators you wire in production) stop being a
grab-bag of names.

## The one-sentence mental model

> **Offline** eval scores fixed cases against a **known answer** *before* you ship (and gates CI);
> **online** eval scores **live traffic** with **reference-free** metrics *after* you ship — and the
> failures you find online become tomorrow's offline cases.

That loop — prod → golden set → gate → prod — is the whole point of having an eval suite at all.
Everything below names the pieces.

## Why this exists (the problem it solves)

"Is the agent good?" isn't one question. An agent can take the **right steps** and still write a
**wrong answer**; it can be **correct** on your test set and **hallucinate** on a question you never
thought to test. So you don't evaluate with one number — you evaluate along two axes:

- **Process vs. outcome** — *did it do the right things* (route, retrieve) vs. *was the answer good*
  (correct, grounded).
- **Offline vs. online** — *before deploy on fixed data with reference answers* vs. *in production on
  real, unlabeled traffic*.

Cross those and you get the three layers below. None replaces the others.

## The three layers

1. **Deterministic (process)** — free, fast, every commit. Checks the **trajectory**: did the
   supervisor delegate to the right sub-agent and call the right tool? It compares tool **names**, not
   answer text, so it's cheap and stable. In this repo: the custom metrics `delegated_and_retrieved`
   (KB branch) and `routed_to_web_search` (web branch) in `eval/test_fast.py`.

2. **LLM-as-judge, offline (outcome vs. references)** — a *second* LLM scores answer **quality**
   against curated reference answers. Costs money + latency, so you run it pre-merge, not per commit.
   In this repo: `final_response_match_v2` (semantic match to a reference) and `hallucinations_v1`
   (groundedness) in `eval/test_judge.py`.

3. **LLM-as-judge, online (outcome on live traffic)** — the same idea on **production** traffic, where
   there is **no reference answer**, so only **reference-free** metrics work. In this repo we run
   **Context Relevance** (the retrieval step, on the `search_article` span). Configured in Langfuse —
   see the **cloud deploy guide, Step 12**.

## The metrics, one paragraph each

- **Trajectory / tool metrics** (`delegated_and_retrieved`, `routed_to_web_search`) — process checks.
  "Did the expected tools fire, by name?" We deliberately ignore tool *arguments* (the model-generated
  query varies every run) and answer text (that's the judge's job). Stable enough to gate every push.

- **`final_response_match_v2`** (offline, **reference-based**) — an LLM judge asks *"does the agent's
  answer mean the same as this curated reference?"* — robust to phrasing, unlike word-overlap (ROUGE).
  Needs a golden answer, so it's **offline only**.

- **`hallucinations_v1` / groundedness** (offline, **reference-free**) — does every claim in the answer
  trace back to the **retrieved context**? It needs the chunks, not a golden answer. This is the
  offline cousin of online Faithfulness.

- **`Faithfulness`** (the canonical groundedness metric, **reference-free**) — decompose the answer
  into claims, verify each against the retrieved chunks, score = fraction supported. Same *concept* as
  `hallucinations_v1`. **Caveat for our setup:** it needs the answer **and** the chunks *together*,
  which live on **different spans** of an ADK trace — and Langfuse's managed evaluators score **one span
  at a time** — so we keep full Faithfulness **offline** (`hallucinations_v1`) and use **Context
  Relevance** as the online retrieval signal.

- **`Context Relevance`** (online, **reference-free**) — are the retrieved chunks relevant to the query?
  Runs **observation-level**, on the **`search_article` span** (query + chunks) — a judge right on the
  tool call. The online stand-in for the retrieval half of groundedness.

> **Reference-based vs. reference-free is the key online constraint.** Live traffic has no golden
> answer, so reference-based metrics (`final_response_match_v2`, RAGAS `Answer Correctness`,
> `Context Recall`) **can't run online** — they belong in the offline golden set. Only metrics that
> score against the *question* and the *retrieved context* — **Context Relevance**,
> groundedness — work in production.

## The judge ≠ agent principle

The evaluator LLM is **independent of the agent under test** — different model, often different
provider (here: a **Gemini** judge scoring a **Cohere** agent). Grading your own homework with the
same model and prompt just launders its biases; a separate judge is the cheap way to stay honest. Same
principle offline and online.

## How it shows up in this repo

`eval/test_fast.py` (deterministic) + `eval/test_judge.py` (judge) are run as a **regression gate**
by `eval/compare_to_baseline.py`, which fails the build if any criterion drops below the floor
committed in `baseline.json` — wire that same command into CI to gate merges. **Online** scoring
lives in Langfuse (the cloud deploy guide, **Step 12**), not in the repo.

## Closing the loop (the whole point)

Online scoring surfaces a low-scoring production trace → you copy the question and write a
corrected reference answer → it goes into the offline golden set
(`eval/fixtures_judge/report_qa.evalset.json`) → the offline judge now guards that case forever,
and CI enforces it. **Traces become eval cases; eval failures tell you what to trace.**

## Two practical notes

- **Judging costs money — sample, don't score everything.** Every judged trace is an extra LLM call,
  and RAGAS is **multi-call** (Faithfulness scores each claim separately). Online: sample ~5–10%.
  Offline: keep `num_runs` low and gate the cheap deterministic suite on every commit, the judge suite
  pre-merge.
- **Mind score direction — and verify it.** Most metrics run **0→1, higher = better**, so a *low*
  score (e.g. `< 0.7`) is the one to act on. But **don't assume**: some Langfuse *managed* templates
  are inverted or misnamed relative to what their name implies. **Always read a score's comment
  against its value** before you trust it, or use a **custom `1 = good` prompt**.

## Glossary (one-liners)

| Term | Meaning |
|---|---|
| **Offline eval** | Score fixed cases before deploy; gates CI |
| **Online eval** | Score live production traffic after deploy (sampled, async) |
| **Process metric** | Did it take the right steps? (tool names / trajectory) |
| **Outcome metric** | Was the answer good? (correct / grounded / relevant) |
| **Reference-based** | Needs a curated golden answer (offline only) |
| **Reference-free** | Scores vs. question + retrieved context (works online) |
| **LLM-as-judge** | A second, independent LLM grades the answer |
| **Groundedness / Faithfulness** | Are the answer's claims supported by the retrieved chunks? (offline here) |
| **Context Relevance** | Are the retrieved chunks relevant to the query? (online, on the `search_article` span) |
| **Regression gate** | Fail the build if a metric drops below a committed baseline |
| **Golden set** | The curated offline dataset of question + reference answer |
| **Close the loop** | Promote failing production traces into the golden set |

## References

- RAGAS — metrics overview: <https://docs.ragas.io/en/stable/concepts/metrics/>
- RAGAS — Faithfulness: <https://docs.ragas.io/en/stable/concepts/metrics/available_metrics/faithfulness/>
- Langfuse — LLM-as-a-judge / evaluators: <https://langfuse.com/docs/evaluation/>
- Google ADK — evaluation: <https://adk.dev/evaluate/>
- In this repo: the cloud deploy guide **Step 12** (online setup) · `eval/` (offline suite) ·
  [`observability_concepts.md`](observability_concepts.md) (the telemetry companion)
