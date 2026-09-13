# Deploy to Google Cloud Run

> The same two-container system you ran locally — the **agent** + the **MCP Search sidecar** — now
> on Cloud Run, backed by **Qdrant Cloud** and **Cloud SQL**, with **traces** and **online eval**.

This guide assumes you've completed the **Prerequisites** and **Setup (one-time)** sections of
[`../README.md`](../README.md) — a `.env` filled in and sourced, and a GCP project with billing.

> ⚠️ **Load `.env` into *this* shell first.** Every step below relies on `$PROJECT_ID`, `$REGION`,
> your Langfuse keys, etc. — and env vars don't persist across terminals. If you opened a fresh one
> for this guide (or skipped this in the README), re-run it from the repo root (and any new terminal):
> ```bash
> set -a; source .env; set +a
> ```

> 🔁 **You're redeploying.** Observability deps + instrumentation live *inside the image*, along with
> the eval suite, so you rebuild and roll a new revision. The steps below build the new image.

> 🚫 **No auth on the endpoint.** We deploy public (`run.invoker` for `allUsers`) so the browser
> chat UI works without GCP credentials. Real production puts IAP/OAuth in front. **Coming later.**

## Step 1 — Enable the APIs

```bash
gcloud config set project "$PROJECT_ID"
gcloud services enable \
    run.googleapis.com \
    cloudbuild.googleapis.com \
    artifactregistry.googleapis.com \
    secretmanager.googleapis.com \
    sqladmin.googleapis.com
```

## Step 2 — Cloud SQL for sessions

```bash
# 2a. The Postgres instance. Takes ~5 minutes — grab a coffee.
gcloud sql instances create "$INSTANCE_NAME" \
    --database-version=POSTGRES_15 \
    --tier=db-f1-micro \
    --region="$REGION"

# 2b. The `sessions` database ADK writes to.
gcloud sql databases create sessions --instance="$INSTANCE_NAME"

# 2c. The `app` user with the password from $DB_PASS.
gcloud sql users create "$DB_USER" \
    --instance="$INSTANCE_NAME" \
    --password="$DB_PASS"
```

## Step 3 — Push the secrets to Secret Manager

### 3a. The five app secrets

Use `printf '%s'` (not `echo -n`) so no trailing newline sneaks into a key — a `\n` in a Cohere key
or a Qdrant URL causes header-injection / malformed-URL errors at runtime.

```bash
printf '%s' "$COHERE_API_KEY" | gcloud secrets create COHERE_API_KEY --data-file=-
printf '%s' "$QDRANT_URL"     | gcloud secrets create QDRANT_URL     --data-file=-
printf '%s' "$QDRANT_API_KEY" | gcloud secrets create QDRANT_API_KEY --data-file=-
printf '%s' "$EXAAI_API_KEY"  | gcloud secrets create EXAAI_API_KEY  --data-file=-
printf '%s' "$DB_PASS"        | gcloud secrets create DB_PASS        --data-file=-
```

> 💡 Re-running after a secret exists? Switch `create` to `versions add`.

### 3b. The Langfuse tracing secret (REQUIRED)

Production tracing goes to **Langfuse**, and it's **not optional** — `service.yaml` references these
secrets, so the deploy in Step 8 **fails without them**. **One Langfuse project covers everything** —
local dev, this production deploy, *and* a future CI pipeline. Create a single project named
**`multi-agent-rag-search-observability`** and reuse the same keys everywhere. (Prod tags its traces with
`LANGFUSE_TRACING_ENVIRONMENT=production` — set in `service.yaml` — so you can still tell prod apart
from lab traffic in the one project. Prefer full isolation? Use a separate project — optional.)

Grab the project's **Public** (`pk-lf-…`) and **Secret** (`sk-lf-…`) keys at <https://cloud.langfuse.com>,
put them in your **`.env`** as `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` (already loaded in Setup),
and store them as two secrets — the agent's Langfuse SDK reads them directly:

```bash
printf '%s' "$LANGFUSE_PUBLIC_KEY" | gcloud secrets create LANGFUSE_PUBLIC_KEY --data-file=-
printf '%s' "$LANGFUSE_SECRET_KEY" | gcloud secrets create LANGFUSE_SECRET_KEY --data-file=-
```

> US region? Set `LANGFUSE_HOST` to `https://us.cloud.langfuse.com` in `service.yaml.template` (then
> re-run `make configure-service`).

Secrets vs config: the Langfuse **keys** live in Secret Manager (above); non-sensitive settings
(`LANGFUSE_HOST`, `LANGFUSE_TRACING_ENVIRONMENT`, `CLOUDSQL_INSTANCE`, `DB_USER`, `QDRANT_COLLECTION`)
live in `service.yaml`.

## Step 4 — Service account (least privilege)

A dedicated identity that can read the secrets it needs and reach the DB — nothing more. Deleting it
later removes every binding with it.

```bash
export SA_NAME="agent-assistant-identity"
export SA_EMAIL="$SA_NAME@$PROJECT_ID.iam.gserviceaccount.com"

gcloud iam service-accounts create "$SA_NAME" --display-name="Agent Assistant SA"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:$SA_EMAIL" --role="roles/cloudsql.client"
```

## Step 5 — Build the images

The agent and the MCP sidecar are **separate images**, each built from its own folder (its own
`Dockerfile` + `pyproject.toml`). Build both:

```bash
gcloud builds submit --tag gcr.io/$PROJECT_ID/agent ./agent
gcloud builds submit --tag gcr.io/$PROJECT_ID/mcp   ./mcp
```

First builds ~3–4 min each; later builds hit the layer cache. (The agent image bakes in the
observability deps; the eval tooling is an optional extra and is **not** in the image.)

## Step 6 — Ingest into Qdrant Cloud

A **Cloud Run Job** (not a service) loads the pre-processed corpus into Qdrant Cloud, then exits.

```bash
# 6a. Build the ingestion image.
gcloud builds submit --tag gcr.io/$PROJECT_ID/ingestion-job ./ingestion

# 6b. Deploy + run the job. Secrets come from Secret Manager, not the command line.
#     RETRIEVAL_MODE must match what you'll set on the mcp container in service.yaml (Step 7) —
#     ingestion and mcp write/read the same "<QDRANT_COLLECTION>_<RETRIEVAL_MODE>" collection, so a
#     mismatch here means mcp silently queries a collection ingestion never wrote to.
#     hybrid mode only: COHERE_API_KEY (embeds the PDF text) and INGEST_PDF_FILENAME (which PDF in
#     ingestion/files/ to chunk — must exist in that folder before the Step 6a build) are required.
#     visual mode: pre-baked jsonl/safetensors pair drives ingestion — set INGEST_DOC_BASENAME if
#     that pair's basename differs from INGEST_PDF_FILENAME's (it defaults to the PDF's basename).
gcloud run jobs deploy ingestion-job \
    --image gcr.io/$PROJECT_ID/ingestion-job \
    --region "$REGION" \
    --service-account "$SA_EMAIL" \
    --set-env-vars="QDRANT_COLLECTION=$QDRANT_COLLECTION,RETRIEVAL_MODE=$RETRIEVAL_MODE,INGEST_PDF_FILENAME=$INGEST_PDF_FILENAME,INGEST_DOC_BASENAME=$INGEST_DOC_BASENAME" \
    --set-secrets="QDRANT_URL=QDRANT_URL:latest,QDRANT_API_KEY=QDRANT_API_KEY:latest,COHERE_API_KEY=COHERE_API_KEY:latest" \
    --execute-now
```

## Step 7 — Generate `service.yaml`

`service.yaml` is the deployment blueprint: one **Service**, two **containers** —

- **agent** (port 8080) with env + secret refs and the Cloud SQL instance annotation. It reaches the
  sidecar at `MCP_SERVER_URL=http://localhost:3000/mcp` (same pod, localhost).
- **mcp** — runs `python /app/mcp/main.py` on `:3000`.

You don't edit `service.yaml` directly — it's **generated** from the committed
[`service.yaml.template`](../service.yaml.template) and your `.env`. From the repo root:

```bash
make configure-service
```

This renders `service.yaml`, substituting only the project-identity values — `PROJECT_ID`,
`CLOUDSQL_INSTANCE`, `DB_USER`, `QDRANT_COLLECTION` — via `envsubst` (install with `brew install
gettext` if missing). It's **idempotent**: re-run it any time your `.env` changes. `service.yaml` is
git-ignored (it carries your project ID); the template is the source of truth.

> ℹ️ The **observability block is hardcoded in the template** (tracing ON → Langfuse), *not* taken
> from `.env` — because `.env` is your **local** config (tracing off, Phoenix) and `service.yaml` is
> **production**. US region? Set `LANGFUSE_HOST` to `https://us.cloud.langfuse.com` in the template
> and re-run `make configure-service`.

> ⚠️ **Check the `mcp` container's env before deploying.** The MCP server does the embedding +
> Qdrant search, so it needs `COHERE_API_KEY`, **`QDRANT_URL`**, **`QDRANT_API_KEY`**, and
> `QDRANT_COLLECTION`. Make sure all four are present on the `mcp` container in `service.yaml`
> (as secret refs / env), not just on the `agent` container — otherwise cloud search returns errors.

## Step 8 — Deploy

```bash
gcloud run services replace service.yaml --region "$REGION"
```

`replace` applies the whole manifest declaratively — both containers, env, secrets, the Cloud SQL
connection — in one shot.

## Step 9 — Make it reachable

```bash
gcloud run services add-iam-policy-binding multi-agent-rag-search \
    --member="allUsers" --role="roles/run.invoker" --region="$REGION"
```

> Prefer a private demo? Replace `allUsers` with `user:you@example.com` (the user must be signed
> into that Google account in the browser).

## Step 10 — Use it

```bash
gcloud run services describe multi-agent-rag-search \
    --region="$REGION" --format='value(status.url)'
```

Append `/dev-ui` — the same chat you used locally, now backed by Qdrant Cloud + Cloud SQL. Ask the
report question; sessions persist across redeploys and scale-to-zero.

## Step 11 — Verify production observability (Langfuse)

Tracing is **already wired and required** — `service.yaml` has the Langfuse block active on the
agent container, and you created the `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` secrets back in
**Step 3b**. So there's nothing to enable here; the deploy you just ran is already exporting traces.
This step is to **confirm it works** — every student should see traces in Langfuse.

*Why Langfuse in production?* Same agent code, same OpenInference instrumentation as local — you
just send the same spans to a **hosted, persistent** backend: traces survive restarts, history
is kept, cost is tracked over time, a team shares one dashboard, and you can run UI online-evals
(Step 12). Phoenix's local container is ephemeral — perfect for dev, wrong for prod.

**Verify:** hit the service URL `/dev-ui`, ask a question, and within a few seconds the trace
(supervisor → `transfer_to_agent` → sidecar `search_article` → synthesis, with tokens + latency)
appears in your Langfuse dashboard.

**If no trace shows up:**
- Check the revision logs for the `Observability enabled → Langfuse` line from `observability.py`:
  `gcloud run services logs read multi-agent-rag-search --region "$REGION" | grep -i observability`.
- Confirm the deploy didn't fail on a missing secret — `LANGFUSE_PUBLIC_KEY` and `LANGFUSE_SECRET_KEY`
  must exist (Step 3b) and the runtime SA must have `secretmanager.secretAccessor` (Step 4).
- US region? Set `LANGFUSE_HOST=https://us.cloud.langfuse.com` in `service.yaml.template` (then re-run
  `make configure-service`).

> 🔒 **Privacy:** add `OPENINFERENCE_HIDE_INPUTS=true` / `OPENINFERENCE_HIDE_OUTPUTS=true` (env on the
> agent container) to keep trace structure without prompt/response payloads. *Trace structure always,
> payloads selectively.* Concepts: [`observability_concepts.md`](observability_concepts.md).

### A note on Cloud Trace (and why we chose a dedicated tool)

Because the agent emits **standard OpenTelemetry**, you *could* point these same spans at **Google
Cloud Trace** — the GCP-native, no-extra-account option — in one of two ways:

- **ADK CLI path:** run the agent with `adk web --otel_to_cloud agent/` (ADK ≥ 1.17.0), which ships
  logs + traces to Google Cloud Observability (not the older `--trace_to_cloud`).
- **From our FastAPI deploy:** run an **OTel Collector** sidecar that fans out to *both* Cloud Trace
  and Langfuse — see [`observability_concepts.md`](observability_concepts.md).

**We don't wire either in this course, on purpose** — production exports to **Langfuse only**, so
Trace Explorer won't show the agent spans unless you add one of the paths above. Here's the decision:
Cloud Trace, like any generic APM (SigNoz, Jaeger), shows you *spans and latency* — great for "where
did the time go / what called what." We **deliberately chose a dedicated LLM-observability tool** —
**Langfuse** in production, **Phoenix** locally — because it reads the *same* OTel/OpenInference spans
but adds the things that matter for agents: **prompt/response inspection**, **token & cost** tracking,
**dataset capture**, and **LLM-as-judge online evals** (Step 12). Same telemetry underneath; a richer,
agent-aware UI on top — clear benefits for this work. If you'd rather keep everything inside GCP,
Cloud Trace is a fine choice; just add one of the two paths above.

## Step 12 — Online evaluation (score live traffic)

Everything in [`../eval/`](../eval/) is **offline** — fixed cases scored *before* you ship (and gated
in CI). **Online evaluation** scores the **real sessions your agent already served**, so you can watch
groundedness on live traffic. The **golden rule:** never block the user's
request on an LLM judge — online eval **samples traces asynchronously** and scores them after the fact.
(This is production-only: locally you have nothing but your own dev-UI clicks.)

We score live traffic with a **reference-free** judge (no golden answer needed, so it works on real
traffic), powered by your Gemini-on-Vertex judge:

- **Context Relevance** — are the chunks `search_article` retrieved relevant to the query?

> **Why not `Faithfulness`** (answer grounded in the retrieved context)? It needs the **final answer**
> *and* the **retrieved chunks** *together* — but in an ADK trace those live in **two different spans**,
> and a managed evaluator scores **one span at a time**, so it can't map across them. **Context
> Relevance** is the practical, reachable single-span signal: it covers *"did we retrieve good
> material?"*. (Concepts: [`evaluation_concepts.md`](evaluation_concepts.md).)

### 12a. Set it up in Langfuse (one time, no code)

**Key idea — point the evaluator at the span that holds its data.** Langfuse's managed evaluators run
**per-observation** (one span; it's the faster, recommended mode), so target:

- **Context Relevance → the `search_article` span** — it carries the query (**input**) and the retrieved
  chunks (**output**).

(Both must be present on the span — they are, unless Step 11's `OPENINFERENCE_HIDE_INPUTS/OUTPUTS` is on,
which blinds the judge.)

In the Langfuse dashboard:

1. **Connect the judge.** Settings → **LLM Connections** → add **Vertex AI** (the Gemini-on-Vertex judge
   from 12b; it must support structured output). Independent of the Cohere agent — same "judge ≠ agent"
   principle as the offline suite.
2. **Evaluators → + Set up evaluator.** Filter **environment = `production`**,
   target **observations by name**, set **sampling** (~10%), and map the variables (these JSONPaths match
   our ADK trace shape exactly):

   **Context Relevance** — target observation **name = `search_article`**:
   | Variable | Object | Field | JSONPath |
   |---|---|---|---|
   | query | Observation | Input | `$.query` |
   | context | Observation | Output | `$.content[0].text` |

3. **Save with Target = new/live traces.** Each evaluator then fires **automatically and asynchronously
   on every new matching span** (at your sampling rate); scores appear on the trace's **Scores** tab a few
   seconds after each request. It's **forward-only** — it scores traces created *after* you save it (use
   the "run on existing traces" option to backfill). Tip: set sampling to **100%** while testing so your
   next request is guaranteed a score, then drop it back to ~10%.

> ⚠️ **Mind the scale — managed RAGAS templates can read backwards.** Some Langfuse managed templates
> are inverted or misnamed relative to what their name implies, so a perfectly good result can score **0**
> while the score's *comment* describes it positively. **Always sanity-check a metric by reading its
> comment against its value.** To keep scores intuitive (and easy to teach/dashboard), prefer a **custom
> NUMERIC evaluator** with an explicit `1 = good` rubric:
> - *Context Relevance:* "Given the query `{{query}}` and retrieved results `{{context}}`, score 0–1 how
>   relevant the results are to the query. Return the score and a one-line reason."

> ℹ️ Langfuse's UI labels shift between versions (Evaluators may sit under "Evaluation"); treat step
> names as the shape, not gospel. Managed evaluators are real LLM calls that **cost money** — keep
> sampling low. Authoritative steps:
> <https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge>.

### 12b. Connecting Vertex AI (Gemini) as the judge — the GCP service-account key

Step 12a's **LLM Connections → Vertex AI** asks for a **GCP service-account JSON key** with the
**Vertex AI User** role (the same Gemini-on-Vertex judge the offline suite uses). Create a
least-privilege one with `gcloud` (your `.env` is already loaded from Setup):

```bash
# Use LF_SA_* here (NOT SA_NAME/SA_EMAIL) so we don't clobber the runtime SA vars from Step 4.
LF_SA_NAME="langfuse-vertex"
LF_SA_EMAIL="${LF_SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

gcloud services enable aiplatform.googleapis.com --project="$PROJECT_ID"   # one-time

# 1. Create the service account
gcloud iam service-accounts create "$LF_SA_NAME" \
    --project="$PROJECT_ID" \
    --display-name="Langfuse Vertex AI online scoring"

# 2. Grant ONLY Vertex AI User (least privilege — not Editor/Owner)
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${LF_SA_EMAIL}" \
    --role="roles/aiplatform.user"

# 3. Mint the JSON key — paste THIS file's contents into the Langfuse field
gcloud iam service-accounts keys create ./langfuse-vertex-key.json \
    --iam-account="$LF_SA_EMAIL"
```

Paste the contents of `langfuse-vertex-key.json` into Langfuse, then **delete the local file** —
Langfuse stores it encrypted server-side:

```bash
rm ./langfuse-vertex-key.json
```

> 🛑 **If `keys create` fails with `constraints/iam.disableServiceAccountKeyCreation`**, your org
> disables downloadable SA keys by policy. Options:
> - **Preferred — skip Vertex entirely:** connect the judge with a **Google AI Studio (Gemini) API
>   key** (<https://aistudio.google.com/apikey>) or an OpenAI/Anthropic key instead. No SA key, no
>   policy change, same `gemini-2.5-flash` judge. (Mind AI Studio's ~20 req/day free-tier cap — keep
>   sampling low or use a paid key.)
> - **Override the policy** (only with org/project **Org Policy Admin**): Console → **IAM & Admin →
>   Organization Policies** → filter `iam.disableServiceAccountKeyCreation` → **Manage policy** →
>   *Override parent's policy* → rule with **Enforcement: Off** → **Set policy** (propagation up to
>   ~1 hour). **Re-enable it once the key exists** — project-wide key creation is a standing risk.

> 🔒 **Key hygiene.** That JSON is a long-lived credential: never commit it, keep it at
> `roles/aiplatform.user`, and rotate/revoke later with
> `gcloud iam service-accounts keys list --iam-account="$LF_SA_EMAIL"` →
> `... keys delete <KEY_ID> --iam-account="$LF_SA_EMAIL"`.

### 12c. Close the loop

- **Dashboard** the mean groundedness over time in Langfuse — that's your live quality SLI.
- **Close the loop (the whole point of having online eval):** filter Langfuse for low-scoring sessions
  and promote them — question + a corrected reference answer — into the golden eval set
  ([`../eval/fixtures_judge/report_qa.evalset.json`](../eval/fixtures_judge/report_qa.evalset.json)).
  Now your **offline** gate guards against that regression forever, and CI can enforce it too.
  *Traces become eval cases; eval failures tell you what to trace.*

> **Prefer code over the UI?** Do the same sample → judge → write-back loop as a scheduled **Cloud Run
> Job** (Cloud Scheduler): fetch recent traces with the Langfuse SDK, reconstruct *question + retrieved
> chunks + answer*, run a Gemini groundedness judge, and write the score back with
> `langfuse.create_score(trace_id=…, name="groundedness", value=…)`. (For *acting* on a bad answer in
> real time — block/flag an ungrounded response — that's an inline guardrail, an ADK
> `after_model_callback`; it adds a judge call to every request, so use it only when you must intervene
> live, not for monitoring.)

## Step 13 — Tail the logs

```bash
gcloud run services logs tail multi-agent-rag-search --region="$REGION"
```

## Redeploying after a change

Rebuild the image you changed, then re-apply the manifest — e.g. for an agent change:
`gcloud builds submit --tag gcr.io/$PROJECT_ID/agent ./agent` (or `…/mcp ./mcp` for the sidecar),
then `gcloud run services replace service.yaml --region "$REGION"`. Cloud Run rolls a new revision
with zero downtime.

---

## When you're done — clean up

Cloud Run is free when idle, but Cloud SQL and stored images are not.

```bash
gcloud run services delete multi-agent-rag-search --region="$REGION" --quiet
gcloud run jobs delete ingestion-job --region="$REGION" --quiet
gcloud sql instances delete "$INSTANCE_NAME" --quiet
gcloud secrets delete COHERE_API_KEY --quiet
gcloud secrets delete QDRANT_URL     --quiet
gcloud secrets delete QDRANT_API_KEY --quiet
gcloud secrets delete EXAAI_API_KEY  --quiet
gcloud secrets delete DB_PASS        --quiet
gcloud secrets delete LANGFUSE_PUBLIC_KEY --quiet
gcloud secrets delete LANGFUSE_SECRET_KEY --quiet
gcloud iam service-accounts delete "agent-assistant-identity@$PROJECT_ID.iam.gserviceaccount.com" --quiet   # the RUNTIME SA — NOT langfuse-vertex (named explicitly so a clobbered $SA_EMAIL can't delete the wrong one)
```

> 🔁 **Keep the `langfuse-vertex` SA around — don't delete it here.** Its JSON key is stored
> inside Langfuse (Step 12b) and powers the **online evaluator**; deleting the SA *revokes that key* and
> silently breaks online scoring, forcing you to redo the whole 12b key-paste later. Leaving it is
> low-risk: it's **least-privilege** (`roles/aiplatform.user`) and the key lives only in Langfuse (you
> already `rm`'d the local copy). The SA deleted above is the *runtime* `agent-assistant-identity` — a
> different account. (The `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY` secrets are quick to recreate via Step 3b on
> your next deploy; keep them too if you redeploy often.)

---

## Troubleshooting

**Deploy fails with "Cloud SQL instance not found".** Step 2 hasn't finished (~5 min). Watch:
`gcloud sql operations list --instance=$INSTANCE_NAME`.

**Agent returns an MCP/search error in the cloud.** The `mcp` container is missing Qdrant creds —
confirm `QDRANT_URL` + `QDRANT_API_KEY` are set on the **mcp** container in `service.yaml` (Step 7),
not only the agent.

**"Permission denied on secret" at startup.** The runtime SA is missing
`secretmanager.secretAccessor` (Step 4), or the revision isn't running as `$SA_EMAIL`. Verify:
`gcloud run services describe multi-agent-rag-search --region="$REGION" --format='value(spec.template.spec.serviceAccountName)'`.

**Sessions don't persist.** Usually the Cloud SQL annotation / `CLOUDSQL_INSTANCE` is wrong, or the
SA lacks `cloudsql.client`. Confirm the `run.googleapis.com/cloudsql-instances` annotation in
`service.yaml` matches `$CLOUDSQL_INSTANCE`.

**No traces in your backend.** Confirm `OBSERVABILITY_ENABLED=true` and the OTLP endpoint/headers on
the **agent** container; check the revision logs for the `Observability enabled → exporting OTLP…`
line from `observability.py`.

**Traces appear, but slowly / minutes late.** Sending spans to Langfuse over raw **OTLP** can take
minutes to *render* in the Langfuse UI. That's why production uses the **Langfuse SDK** (the
`LANGFUSE_*` env in `service.yaml`), which renders in seconds. `cpu-throttling: "false"` is also set so
the SDK's background exporter can flush between requests on Cloud Run. (Local dev uses Phoenix over
OTLP, where rendering is instant.)

**Logs spamming `Failed to export metrics batch code: 404`.** Langfuse is **traces-only**, but ADK's
`get_fast_api_app()` auto-creates an OTLP *metrics* exporter whenever an `OTEL_EXPORTER_OTLP_*_ENDPOINT`
env var is present, and it POSTs metrics to a `/v1/metrics` path Langfuse doesn't have. The agent's
`observability.py` disables ADK's env-driven telemetry (so only our OpenInference *traces* are sent) —
if you see this 404, you're on an older image: **rebuild and redeploy the agent** (Step 5 → Step 8).

**Cold-start OOM.** Bump the agent container's memory in `service.yaml.template` (then re-run `make configure-service`). The ADK runtime + Cohere +
Qdrant clients share a process; the MCP sidecar adds its own footprint.
