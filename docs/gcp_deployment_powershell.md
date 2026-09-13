# Deploy to Google Cloud Run — Windows / PowerShell

> 🪟 **PowerShell edition of [`gcp_deployment.md`](gcp_deployment.md).** Same steps, commands
> rewritten for Windows PowerShell. On macOS/Linux use [`gcp_deployment.md`](gcp_deployment.md).

This guide assumes you've completed the **Prerequisites** and **Setup (one-time)** sections of
[`../README_powershell.md`](../README_powershell.md) — a `.env` filled in and loaded, and a GCP
project with billing.

> ⚠️ **Load `.env` into *this* shell first.** Every step below relies on `$env:PROJECT_ID`,
> `$env:REGION`, your Langfuse keys, etc. — and env vars don't persist across terminals. PowerShell
> has no `source`; if you opened a fresh terminal (or skipped this in the README), re-run the loader
> from the repo root (and any new terminal):
> ```powershell
> Get-Content .env | Where-Object { $_ -match '^\s*[^#].+=' } | ForEach-Object {
>     $name, $value = $_ -split '=', 2
>     Set-Item -Path "Env:$($name.Trim())" -Value $value.Trim()
> }
> ```

> 🔁 **You're redeploying** — observability + an eval suite live inside the image, so you
> rebuild and roll a new revision.
> 🚫 **No auth on the endpoint** — deployed public for the lab; real auth comes later.

## Step 1 — Enable the APIs

```powershell
gcloud config set project $env:PROJECT_ID
gcloud services enable `
    run.googleapis.com `
    cloudbuild.googleapis.com `
    artifactregistry.googleapis.com `
    secretmanager.googleapis.com `
    sqladmin.googleapis.com
```

## Step 2 — Cloud SQL for sessions

```powershell
# 2a. The Postgres instance. Takes ~5 minutes.
gcloud sql instances create $env:INSTANCE_NAME `
    --database-version=POSTGRES_15 `
    --tier=db-f1-micro `
    --region=$env:REGION

# 2b. The `sessions` database.
gcloud sql databases create sessions --instance=$env:INSTANCE_NAME

# 2c. The `app` user.
gcloud sql users create $env:DB_USER `
    --instance=$env:INSTANCE_NAME `
    --password=$env:DB_PASS
```

## Step 3 — Push the secrets to Secret Manager

PowerShell's `Set-Content -NoNewline` writes the value with **no trailing newline** (a `\n` in a key
or URL causes runtime errors), then we hand the file to `gcloud`:

```powershell
function New-Secret($name, $value) {
    $tmp = New-TemporaryFile
    Set-Content -Path $tmp -Value $value -NoNewline -Encoding ascii
    gcloud secrets create $name --data-file=$tmp
    Remove-Item $tmp
}

New-Secret "COHERE_API_KEY" $env:COHERE_API_KEY
New-Secret "QDRANT_URL"     $env:QDRANT_URL
New-Secret "QDRANT_API_KEY" $env:QDRANT_API_KEY
New-Secret "EXAAI_API_KEY"  $env:EXAAI_API_KEY
New-Secret "DB_PASS"        $env:DB_PASS
```

> 💡 Re-running after a secret exists? Replace `secrets create` with `secrets versions add` in the
> function.

### 3b. The Langfuse tracing secret (REQUIRED)

Production tracing goes to **Langfuse** and is **not optional** — `service.yaml` references these
secrets, so the deploy in Step 8 **fails without them**. **One Langfuse project covers everything** —
local dev, this production deploy, *and* a future CI pipeline. Create a single project named
**`multi-agent-rag-search-observability`** and reuse the same keys everywhere. (Prod tags its traces with
`LANGFUSE_TRACING_ENVIRONMENT=production` in `service.yaml`, so it's easy to tell apart from local dev
traffic in the one project. Prefer full isolation? Use a separate project — optional.)

Grab the project's **Public** (`pk-lf-…`) and **Secret** (`sk-lf-…`) keys at <https://cloud.langfuse.com>,
put them in your **`.env`** as `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` (the
[`../README_powershell.md`](../README_powershell.md) loader puts them in `$env:`), and store them as two
secrets — the agent's Langfuse SDK reads them directly:

```powershell
New-Secret "LANGFUSE_PUBLIC_KEY" $env:LANGFUSE_PUBLIC_KEY
New-Secret "LANGFUSE_SECRET_KEY" $env:LANGFUSE_SECRET_KEY
```

> US region? Set `LANGFUSE_HOST` to `https://us.cloud.langfuse.com` in `service.yaml.template` (then re-render — Step 7).

Secrets vs config: keys live in Secret Manager; non-sensitive settings (`CLOUDSQL_INSTANCE`,
`DB_USER`, `QDRANT_COLLECTION`, the Langfuse endpoint) live in `service.yaml`.

## Step 4 — Service account (least privilege)

```powershell
$env:SA_NAME  = "agent-assistant-identity"
$env:SA_EMAIL = "$($env:SA_NAME)@$($env:PROJECT_ID).iam.gserviceaccount.com"

gcloud iam service-accounts create $env:SA_NAME --display-name="Agent Assistant SA"

gcloud projects add-iam-policy-binding $env:PROJECT_ID `
    --member="serviceAccount:$($env:SA_EMAIL)" --role="roles/secretmanager.secretAccessor"

gcloud projects add-iam-policy-binding $env:PROJECT_ID `
    --member="serviceAccount:$($env:SA_EMAIL)" --role="roles/cloudsql.client"
```

## Step 5 — Build the images

The agent and MCP sidecar are **separate images**, each built from its own folder:

```powershell
gcloud builds submit --tag "gcr.io/$($env:PROJECT_ID)/agent" ./agent
gcloud builds submit --tag "gcr.io/$($env:PROJECT_ID)/mcp"   ./mcp
```

## Step 6 — Ingest into Qdrant Cloud

```powershell
gcloud builds submit --tag "gcr.io/$($env:PROJECT_ID)/ingestion-job" ./ingestion

# RETRIEVAL_MODE must match what you'll set on the mcp container in service.yaml (Step 7) —
# ingestion and mcp write/read the same "<QDRANT_COLLECTION>_<RETRIEVAL_MODE>" collection, so a
# mismatch here means mcp silently queries a collection ingestion never wrote to.
# hybrid mode only: COHERE_API_KEY (embeds the PDF text) and INGEST_PDF_FILENAME (which PDF in
# ingestion/files/ to chunk — must exist in that folder before the Step 6a build) are required.
# visual mode: pre-baked jsonl/safetensors pair drives ingestion — set INGEST_DOC_BASENAME if that
# pair's basename differs from INGEST_PDF_FILENAME's (it defaults to the PDF's basename).
gcloud run jobs deploy ingestion-job `
    --image "gcr.io/$($env:PROJECT_ID)/ingestion-job" `
    --region $env:REGION `
    --service-account $env:SA_EMAIL `
    --set-env-vars="QDRANT_COLLECTION=$($env:QDRANT_COLLECTION),RETRIEVAL_MODE=$($env:RETRIEVAL_MODE),INGEST_PDF_FILENAME=$($env:INGEST_PDF_FILENAME),INGEST_DOC_BASENAME=$($env:INGEST_DOC_BASENAME)" `
    --set-secrets="QDRANT_URL=QDRANT_URL:latest,QDRANT_API_KEY=QDRANT_API_KEY:latest,COHERE_API_KEY=COHERE_API_KEY:latest" `
    --execute-now
```

## Step 7 — Generate `service.yaml`

One Service, two containers — **agent** (8080) and **mcp** (`python /app/mcp/main.py` on 3000,
reached at `http://localhost:3000/mcp`). You don't edit `service.yaml` directly — it's **generated**
from the committed [`service.yaml.template`](../service.yaml.template) + your `.env`.

If you have `make` + `envsubst` (WSL / Git Bash): `make configure-service`. Otherwise render it
natively in PowerShell — substitute only the project-identity values (`.env` loaded into `$env:` via
the loader from [`../README_powershell.md`](../README_powershell.md)):

```powershell
(Get-Content service.yaml.template -Raw) `
  -replace '\$\{PROJECT_ID\}',        $env:PROJECT_ID `
  -replace '\$\{CLOUDSQL_INSTANCE\}', $env:CLOUDSQL_INSTANCE `
  -replace '\$\{DB_USER\}',           $env:DB_USER `
  -replace '\$\{QDRANT_COLLECTION\}', $env:QDRANT_COLLECTION `
  -replace '\$\{RETRIEVAL_MODE\}',    $env:RETRIEVAL_MODE |
  Set-Content service.yaml
```

Idempotent — re-run when `.env` changes. `service.yaml` is git-ignored (it carries your project ID).

> ℹ️ The **observability block is hardcoded in the template** (tracing ON → Langfuse), *not* from
> `.env` — `.env` is your **local** config (tracing off, Phoenix), `service.yaml` is **production**.
> US region? Set `LANGFUSE_HOST` to `https://us.cloud.langfuse.com` in the template and re-render.

> ⚠️ **Check the `mcp` container's env.** The MCP server does the embedding + Qdrant search, so it
> needs `COHERE_API_KEY`, **`QDRANT_URL`**, **`QDRANT_API_KEY`**, and `QDRANT_COLLECTION` on the
> **mcp** container in `service.yaml` — not only on the agent — or cloud search returns errors.

## Step 8 — Deploy

```powershell
gcloud run services replace service.yaml --region $env:REGION
```

## Step 9 — Make it reachable

```powershell
gcloud run services add-iam-policy-binding multi-agent-rag-search `
    --member="allUsers" --role="roles/run.invoker" --region=$env:REGION
```

> Private demo? Replace `allUsers` with `user:you@example.com`.

## Step 10 — Use it

```powershell
gcloud run services describe multi-agent-rag-search `
    --region=$env:REGION --format='value(status.url)'
```

Append `/dev-ui` — the chat from local, now on Qdrant Cloud + Cloud SQL.

## Step 11 — Verify production observability (Langfuse)

Tracing is **already wired and required** — `service.yaml` has the Langfuse block active, and you
created the `LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY` secrets in **Step 3b**. Nothing to enable
here; the deploy you just ran is already exporting traces. This step **confirms** it — every student
should see traces in Langfuse.

*Why Langfuse in prod?* Same agent code + OpenInference as local, just pointed at a hosted,
persistent backend (history, cost analytics, team dashboards, UI online-evals). Phoenix's local
container is ephemeral — great for dev, wrong for prod.

**Verify:** hit `/dev-ui`, ask a question — the trace appears in your Langfuse dashboard within seconds.

**No trace?**
```powershell
gcloud run services logs read multi-agent-rag-search --region $env:REGION | Select-String "Observability"
```
Confirm the deploy didn't fail on a missing secret (`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`,
Step 3b), the SA has `secretmanager.secretAccessor` (Step 4), and — US region — `LANGFUSE_HOST` in `service.yaml` points at
the `us.cloud.langfuse.com` host.

> 🔒 Set `OPENINFERENCE_HIDE_INPUTS` / `OPENINFERENCE_HIDE_OUTPUTS` to `"true"` (env on the agent
> container) to redact payloads. Concepts: [`observability_concepts.md`](observability_concepts.md).
>
### A note on Cloud Trace (and why we chose a dedicated tool)

Because it's standard OpenTelemetry, you *could* send these spans to **Cloud Trace** too
(`adk web --otel_to_cloud agent/`, or an OTel Collector that fans out) — but **we don't wire that
here, on purpose**: production exports to **Langfuse only**, so Trace Explorer won't show the agent
spans unless you add one of those paths. Cloud Trace (like any generic APM) shows spans + latency; we
**deliberately chose a dedicated LLM-observability tool** — **Langfuse / Phoenix (Arize)** — which
reads the same spans and adds prompt/response inspection, token & cost, dataset capture, and
LLM-as-judge evals. Use Cloud Trace if you want everything inside GCP. (Full explanation in
[`gcp_deployment.md`](gcp_deployment.md) Step 11.)

## Step 12 — Online evaluation (score live traffic)

Everything in [`../eval/`](../eval/) is **offline** — fixed cases scored *before* you ship. **Online
evaluation** scores the **real sessions your agent already served** so you can watch groundedness on
live traffic. The **golden rule:** never block the request on an LLM judge — online eval **samples
traces asynchronously**. (Production-only: locally you have nothing but your own dev-UI clicks.)

We use a **reference-free** judge (no golden answer needed), on your Gemini-on-Vertex judge:
**Context Relevance** (are the chunks `search_article` retrieved relevant to the query?).

> **Why not `Faithfulness`?** It needs the **answer** *and* the **retrieved chunks** *together*, but in
> an ADK trace those sit in **two different spans**, and a managed evaluator scores **one span at a
> time** — so it can't map across them. **Context Relevance** is the reachable single-span signal.
> (Concepts: [`evaluation_concepts.md`](evaluation_concepts.md).)

### 12a. Set it up in Langfuse (one time, no code)

**Point the evaluator at the span that holds its data.** Langfuse's managed evaluators run
**per-observation** (one span, the recommended mode), so target:
- **Context Relevance → `search_article` span** (query = input, retrieved chunks = output).

(Both must be on the span — they are, unless Step 11's `OPENINFERENCE_HIDE_INPUTS/OUTPUTS` is on.)

1. **Connect the judge.** Settings → **LLM Connections** → add **Vertex AI** (the Gemini-on-Vertex judge
   from 12b; must support structured output). Independent of the Cohere agent.
2. **Evaluators → + Set up evaluator**: filter **environment = `production`**, target
   **observations by name**, set **sampling** (~10%), and map (JSONPaths match our ADK trace exactly):

   **Context Relevance** — observation **name = `search_article`**:
   | Variable | Object | Field | JSONPath |
   |---|---|---|---|
   | query | Observation | Input | `$.query` |
   | context | Observation | Output | `$.content[0].text` |

3. **Save with Target = new/live traces** → each fires automatically + async on every new matching span
   (at your sampling rate); scores show on the trace's **Scores** tab seconds later. It's **forward-only**
   (use "run on existing traces" to backfill). Set sampling to **100%** while testing, then drop to ~10%.

> ⚠️ **Mind the scale.** Some Langfuse managed templates are inverted or misnamed relative to what their
> name implies, so a good result can score **0** while its *comment* describes it positively. Don't be
> fooled — **read the comment against the value.** For intuitive `1 = good` scores, prefer a **custom
> NUMERIC evaluator**:
> - *Context Relevance:* "Given query `{{query}}` and results `{{context}}`, score 0–1 how relevant the
>   results are. Return the score and a one-line reason."

> ℹ️ Langfuse's UI labels shift between versions; treat step names as the shape, not gospel. Managed
> evaluators **cost money** — keep sampling low.
> <https://langfuse.com/docs/evaluation/evaluation-methods/llm-as-a-judge>.

### 12b. Connecting Vertex AI (Gemini) as the judge — the GCP service-account key

Step 12a's **LLM Connections → Vertex AI** asks for a **GCP service-account JSON key** with the
**Vertex AI User** role. Create a least-privilege one (your `.env` is already loaded into `$env:`):

```powershell
# Use LF_SA_* here (NOT SA_NAME/SA_EMAIL) so we don't clobber the runtime SA vars from Step 4.
$env:LF_SA_NAME  = "langfuse-vertex"
$env:LF_SA_EMAIL = "$($env:LF_SA_NAME)@$($env:PROJECT_ID).iam.gserviceaccount.com"

gcloud services enable aiplatform.googleapis.com --project="$($env:PROJECT_ID)"   # one-time

# 1. Create the service account
gcloud iam service-accounts create $env:LF_SA_NAME `
    --project="$($env:PROJECT_ID)" `
    --display-name="Langfuse Vertex AI online scoring"

# 2. Grant ONLY Vertex AI User (least privilege — not Editor/Owner)
gcloud projects add-iam-policy-binding $env:PROJECT_ID `
    --member="serviceAccount:$($env:LF_SA_EMAIL)" `
    --role="roles/aiplatform.user"

# 3. Mint the JSON key — paste THIS file's contents into the Langfuse field
gcloud iam service-accounts keys create .\langfuse-vertex-key.json `
    --iam-account="$($env:LF_SA_EMAIL)"
```

Paste the contents of `langfuse-vertex-key.json` into Langfuse, then **delete the local file**:

```powershell
Remove-Item .\langfuse-vertex-key.json
```

> 🛑 **If `keys create` fails with `constraints/iam.disableServiceAccountKeyCreation`**, your org
> disables downloadable SA keys. Easiest path: **skip Vertex** and connect the judge with a **Google
> AI Studio (Gemini) API key** (<https://aistudio.google.com/apikey>) or an OpenAI/Anthropic key
> instead — same `gemini-2.5-flash` judge, no SA key (mind AI Studio's ~20 req/day free cap). Or, with
> **Org Policy Admin**, override `iam.disableServiceAccountKeyCreation` to *Enforcement: Off*, create
> the key, then re-enable it.

> 🔒 **Key hygiene.** Never commit the JSON; keep it at `roles/aiplatform.user`; rotate/revoke later
> with `gcloud iam service-accounts keys list/delete --iam-account="$($env:LF_SA_EMAIL)"`.

### 12c. Close the loop

Dashboard mean groundedness over time (your live quality SLI). Then **close the
loop:** filter Langfuse for low-scoring sessions and promote them — question + corrected reference —
into the golden eval set
([`../eval/fixtures_judge/report_qa.evalset.json`](../eval/fixtures_judge/report_qa.evalset.json)), so
your offline gate guards against that regression forever. *Traces become eval cases.*

## Step 13 — Tail the logs

```powershell
gcloud run services logs tail multi-agent-rag-search --region=$env:REGION
```

## Redeploying after a change

Rebuild the image you changed (`./agent` or `./mcp`), then re-apply the manifest:

```powershell
gcloud builds submit --tag "gcr.io/$($env:PROJECT_ID)/agent" ./agent
gcloud run services replace service.yaml --region $env:REGION
```

---

## When you're done — clean up

```powershell
gcloud run services delete multi-agent-rag-search --region=$env:REGION --quiet
gcloud run jobs delete ingestion-job --region=$env:REGION --quiet
gcloud sql instances delete $env:INSTANCE_NAME --quiet
gcloud secrets delete COHERE_API_KEY --quiet
gcloud secrets delete QDRANT_URL     --quiet
gcloud secrets delete QDRANT_API_KEY --quiet
gcloud secrets delete EXAAI_API_KEY  --quiet
gcloud secrets delete DB_PASS        --quiet
gcloud secrets delete LANGFUSE_PUBLIC_KEY --quiet
gcloud secrets delete LANGFUSE_SECRET_KEY --quiet
gcloud iam service-accounts delete "agent-assistant-identity@$($env:PROJECT_ID).iam.gserviceaccount.com" --quiet   # the RUNTIME SA — NOT langfuse-vertex (named explicitly so a clobbered $env:SA_EMAIL can't delete the wrong one)
```

> 🔁 **Keep the `langfuse-vertex` SA around — don't delete it here.** Its JSON key is stored
> inside Langfuse (Step 12b) and powers the **online evaluator**; deleting the SA *revokes that key* and
> silently breaks online scoring, forcing you to redo 12b later. It's low-risk to leave:
> least-privilege (`roles/aiplatform.user`) and the key lives only in Langfuse. The SA deleted above is
> the *runtime* `agent-assistant-identity` — a different account. (The `LANGFUSE_PUBLIC_KEY`/`SECRET_KEY`
> secrets are quick to recreate via Step 3b next deploy.)

---

## Troubleshooting

Same failure modes as the bash guide — see [`gcp_deployment.md` § Troubleshooting](gcp_deployment.md#troubleshooting).
Most common on a fresh deploy: the **mcp** container missing `QDRANT_URL` / `QDRANT_API_KEY` (Step 7),
Cloud SQL still provisioning (Step 2, ~5 min), or the runtime SA missing
`secretmanager.secretAccessor` / `cloudsql.client` (Step 4).
