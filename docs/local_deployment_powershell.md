# Local development with `docker-compose` — Windows / PowerShell

> 🪟 **PowerShell edition of [`local_deployment.md`](local_deployment.md).** Same stack, commands
> rewritten for Windows PowerShell. On macOS/Linux use [`local_deployment.md`](local_deployment.md).

> The whole multi-container system on your laptop — the **agent**, the **MCP Search sidecar**, a
> local **Qdrant**, and a local **Postgres** — plus optional local **Phoenix** tracing and the
> **eval suite**.

This guide assumes you've completed the **Prerequisites** and **Setup (one-time)** sections of
[`../README_powershell.md`](../README_powershell.md) — i.e. you have a `.env` filled in and loaded
into your shell.

> ⚠️ **Load `.env` into *this* shell first.** Env vars don't persist across terminals — if you opened
> a fresh one for this guide (or skipped this in the README), the commands below fail with missing
> keys. PowerShell has no `source`; re-run the loader from the repo root (and any new terminal):
> ```powershell
> Get-Content .env | Where-Object { $_ -match '^\s*[^#].+=' } | ForEach-Object {
>     $name, $value = $_ -split '=', 2
>     Set-Item -Path "Env:$($name.Trim())" -Value $value.Trim()
> }
> ```

> 💡 **`make` users.** Every step shows the raw `docker compose` command **and** the `make`
> equivalent. If you installed `make` (see [README_powershell § Using `make` on Windows](../README_powershell.md))
> use the `make` form to match the videos; otherwise the raw form needs nothing extra.

## 1. Bring the stack up

```powershell
docker compose up --build -d --wait
```
*(`make` equivalent: `make up` — also prints a ready banner.)*

What happens — in order (the compose `depends_on` wiring enforces it):

1. **qdrant** starts on `localhost:6333` and becomes healthy.
2. **ingestion** runs once — loads the pre-processed report (`ingestion/files/`) into the local
   Qdrant, then **exits 0**. One-shot job, not a service.
3. **mcp** starts on `localhost:3000` — the Streamable-HTTP Search server
   (`Application started with StreamableHTTP session manager!`).
4. **postgres** becomes healthy and **agent** starts on `localhost:8080`.
5. `--wait` blocks until all report healthy / completed / running.

Open <http://localhost:8080/dev-ui>.

> 💡 Logs go to the docker daemon: `docker compose logs -f` (`make logs`). Stop with
> `docker compose down` (`make down`).

## 2. Talk to your agent

Open <http://localhost:8080/dev-ui> — ADK's web chat. Try:

- *"What does the report say about the state of AI agents?"* → `kb_agent` calls `search_article` on
  the **MCP sidecar** and answers with a citation.
- *"What's the latest news on AI regulation?"* → `web_search_agent` (Exa).
- Something not in the corpus → the agent should decline (negative rejection).

Sessions persist in Postgres across browser refreshes.

> 💡 `docker compose logs mcp` shows each `search_article` call — the agent calls the sidecar over
> HTTP, exactly like Cloud Run.

## 3. Read the trace — Phoenix (on by default)

**Tracing is on out of the box.** `make up` (or `docker compose up`) already started a local
**Phoenix** trace UI and the agent is already exporting to it — no separate "turn on tracing" step.
Just open the UI.

> **Why it's on (and the switch).** Phoenix is a normal service in `docker-compose.yml` (no profile),
> so it starts with the stack, and the compose file **defaults the agent's tracing env on** locally,
> pointed at the in-network Phoenix service — the same two variables production uses:
> ```yaml
> OBSERVABILITY_ENABLED=${OBSERVABILITY_ENABLED:-true}
> OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_EXPORTER_OTLP_ENDPOINT:-http://phoenix:6006/v1/traces}
> ```
> It uses the service name `phoenix`, not `localhost`, because the containers share the Docker
> network. In production it's the *same toggle*, pointed at Langfuse (deploy guide Step 11). **Don't
> want tracing?** Set `$env:OBSERVABILITY_ENABLED = "false"` (or put it in `.env`) — the agent runs
> untouched. The UI is at <http://localhost:6006>; `docker compose down` (`make down`) stops Phoenix
> with the rest. *(`make trace` still works — it's now an alias for `make up`.)*

Ask a question at `/dev-ui`, then open <http://localhost:6006>:

```
general_assistant
├─ generate_content        ← supervisor decides to delegate
├─ transfer_to_agent       ← hands off to kb_agent
└─ kb_agent
   ├─ search_article       ← HTTP call to the MCP sidecar (click it: query + chunks + latency)
   └─ generate_content     ← the cited answer
```

> 🔒 **Privacy:** `$env:OPENINFERENCE_HIDE_INPUTS = "true"` / `$env:OPENINFERENCE_HIDE_OUTPUTS = "true"`
> redact payloads while keeping the trace shape. Different backend? One env var — point
> `OTEL_EXPORTER_OTLP_ENDPOINT` at Langfuse or a Collector. (Concepts:
> [`observability_concepts.md`](observability_concepts.md).)

### Verify tracing is actually working

```powershell
# 1. Phoenix container up? (starts with `make up`)
docker compose ps phoenix

# 2. Did the export env reach the agent?
docker compose exec agent printenv OBSERVABILITY_ENABLED OTEL_EXPORTER_OTLP_ENDPOINT
docker compose logs agent | Select-String "Observability enabled"

# 3. Are traces arriving? Ask a question at /dev-ui, then refresh the UI (http://localhost:6006).
```
No trace after asking a question = the export isn't reaching Phoenix (recheck step 2, and make sure
`OBSERVABILITY_ENABLED` isn't set to `false` in your `.env`).

### Inspect a trace in Phoenix

In the UI (<http://localhost:6006>), click a trace and walk the tree (supervisor →
`transfer_to_agent` → `search_article` → synthesis). Read the **`search_article` (TOOL)** span (query
+ retrieved chunks — where bad retrieval shows), the **`call_llm`** spans (**input/output tokens** +
latency), and the **root** span (total latency). Automating that "open the worst trace" habit over
live **production** traffic is exactly what a managed Langfuse evaluator does — set it up in
[`gcp_deployment_powershell.md`](gcp_deployment_powershell.md) **Step 12**.

## 4. Run the evaluation suite

With the stack up (MCP reachable at `localhost:3000`), install the eval deps into a dedicated **uv**
virtualenv and run from your host. Using a venv (instead of a bare `pip install`) avoids
`externally-managed-environment` errors and wrong-interpreter surprises; on Windows the venv's Python
is `.venv\Scripts\python.exe`:

```powershell
# Once — create the eval venv and install the [eval] extra into it (install uv first if needed:
#   irm https://astral.sh/uv/install.ps1 | iex ):
uv venv .venv
uv pip install --python .venv "./agent[eval]"

$env:MCP_SERVER_URL = "http://localhost:3000/mcp"
.venv\Scripts\python.exe -m pytest eval/test_fast.py -v     # (make eval) — fast, deterministic
.venv\Scripts\python.exe -m pytest eval/test_judge.py -v    # (make eval-judge) — needs judge auth (see note below)
.venv\Scripts\python.exe eval/compare_to_baseline.py        # (make gate) — regression gate
```

> **Authenticate the judge first (`test_judge.py` / `compare_to_baseline.py --judge` only).** The LLM
> judge runs on **Vertex AI** by default (`.env`: `GOOGLE_GENAI_USE_VERTEXAI=TRUE` +
> `GOOGLE_CLOUD_PROJECT`/`GOOGLE_CLOUD_LOCATION`), so it uses your **gcloud Application Default
> Credentials**, not `$env:GOOGLE_API_KEY`. Log in once (re-run when the token expires — the symptom
> is `RefreshError: Reauthentication is needed`):
>
> ```powershell
> gcloud auth application-default login
> ```
>
> No GCP access? Fall back to AI Studio: set `GOOGLE_GENAI_USE_VERTEXAI=FALSE` and a `GOOGLE_API_KEY`
> in `.env` (mind its ~20 req/day cap — one judge run is ~8 Gemini calls). The deterministic suite
> needs no judge auth.

Full details in [`../eval/README.md`](../eval/README.md).

## 5. Re-ingest on demand

```powershell
docker compose run --rm ingestion               # (make ingest)
```

## 6. Tear down

```powershell
docker compose down --remove-orphans              # (make down) — keep data
docker compose down --volumes --remove-orphans    # (make nuke) — wipe volumes
```

## Troubleshooting

**Ports in use.** Something is on 8080 / 6333 / 5432 / 3000. Stop it or change the host port in
`docker-compose.yml`.

**Agent can't reach the knowledge base.** The agent uses `MCP_SERVER_URL` = `http://mcp:3000/mcp`
in compose (the service name, not `localhost`). If `mcp` failed, `docker compose logs mcp` — usually
a missing `COHERE_API_KEY`.

**First chat returns nothing.** The `ingestion` job hadn't finished. Check `docker compose logs
ingestion`, then `docker compose run --rm ingestion` to re-run.

**Tracing shows nothing in Phoenix.** Tracing is on by default — check you didn't set
`OBSERVABILITY_ENABLED=false`; confirm `docker compose ps phoenix` is up and the UI loads at
<http://localhost:6006>; traces are batched (wait a few seconds).

**`make eval` can't reach the agent/MCP.** `docker compose up` first; eval runs on the host and needs
the sidecar published at `localhost:3000`.

**`LiteLLM:WARNING ... bedrock-runtime ...`** — cosmetic, already silenced in `agent/src/agent.py`.

**`PydanticSerializationError: ... LiteLLMClient`** — known `google-adk` bug
([#5367](https://github.com/google/adk-python/issues/5367)); worked around in `agent/src/agent.py`.
