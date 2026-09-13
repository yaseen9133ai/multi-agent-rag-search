# Local development with `docker-compose`

> The whole multi-container system on your laptop — the **agent**, the **MCP Search sidecar**, a
> local **Qdrant**, and a local **Postgres** — plus an optional local **Phoenix** for tracing and
> the **eval suite**. Iterate without burning Qdrant Cloud or Cloud SQL quota.

This guide assumes you've completed the **Prerequisites** and **Setup (one-time)** sections of
[`../README.md`](../README.md) — i.e. you have a `.env` filled in and sourced into your shell.

> ⚠️ **Load `.env` into *this* shell first.** Env vars don't persist across terminals — if you opened
> a fresh one for this guide (or skipped this in the README), the commands below fail with missing
> keys. Re-run it from the repo root, and any time you open a new terminal:
> ```bash
> set -a; source .env; set +a
> ```

## 1. Bring the stack up

```bash
make up
```
*(raw equivalent: `docker compose up --build -d --wait`)*

What happens — in order (the compose `depends_on` wiring enforces it):

1. **qdrant** starts on `localhost:6333` and becomes healthy.
2. **ingestion** runs once — it loads the pre-processed report (OCR text + embeddings under
   `ingestion/files/`) into the local Qdrant collection, then **exits 0**. This is a one-shot job,
   not a long-running service.
3. **mcp** starts on `localhost:3000` — the Streamable-HTTP Search server. You'll see
   `Application started with StreamableHTTP session manager!` in its logs.
4. **postgres** (sessions) becomes healthy, and **agent** starts on `localhost:8080`.
5. `--wait` blocks until all of the above report healthy / completed / running, then `make up`
   prints a ready banner and returns (detached).

```
✅ Stack is ready. Open http://localhost:8080/dev-ui
```

> 💡 **`make up` is detached.** Logs go to the docker daemon. `make logs` (`docker compose logs -f`)
> to follow them; `make down` to stop.

## 2. Talk to your agent

Open <http://localhost:8080/dev-ui> — ADK's web chat. Try:

- *"What does the report say about the state of AI agents?"* → the supervisor delegates to
  `kb_agent`, which calls `search_article` on the **MCP sidecar** and answers with a citation.
- *"What's the latest news on AI regulation?"* → routes to `web_search_agent` (Exa).
- Something not in the corpus → the agent should say it can't find it (negative rejection).

Session state lives in Postgres — refresh the browser and your history persists.

> 💡 **See the sidecar working:** `docker compose logs mcp` shows each `search_article` call. The
> agent didn't search in-process — it called the sidecar over HTTP, exactly like Cloud Run.

## 3. Read the trace — Phoenix (on by default)

**New in this setup: tracing is on out of the box.** `make up` already started a local **Phoenix**
trace UI alongside the stack, and the agent is already exporting to it — there's no separate "turn on
tracing" step. So all you do here is open the UI.

> **Why it's on by default (and how the switch works).** Phoenix is a regular service in
> `docker-compose.yml` now (no profile), so it starts with `make up`. The agent reads the same two
> environment variables production uses — `OBSERVABILITY_ENABLED` and `OTEL_EXPORTER_OTLP_ENDPOINT` —
> and the compose file **defaults them on locally**, pointed at the in-network Phoenix service:
> ```yaml
> OBSERVABILITY_ENABLED=${OBSERVABILITY_ENABLED:-true}
> OTEL_EXPORTER_OTLP_ENDPOINT=${OTEL_EXPORTER_OTLP_ENDPOINT:-http://phoenix:6006/v1/traces}
> ```
> It uses the **service name** `phoenix`, not `localhost`/`host.docker.internal`, because both
> containers share the Docker network. This is the *exact same toggle* production flips — there it's
> set to `true` and pointed at Langfuse (deploy guide Step 11). **Don't want tracing?** Put
> `OBSERVABILITY_ENABLED=false` in your `.env` and the agent runs untouched (Phoenix still starts but
> stays empty; `observability.py` becomes a no-op). The UI is published at <http://localhost:6006>,
> and `make down` stops Phoenix with the rest.
>
> *(`make trace` still works — it's now just an alias for `make up`, kept for muscle memory.)*

Ask a question at `/dev-ui`, then open <http://localhost:6006>. The trace tree:

```
general_assistant
├─ generate_content        ← supervisor decides to delegate
├─ transfer_to_agent       ← hands off to kb_agent
└─ kb_agent
   ├─ search_article       ← HTTP call to the MCP sidecar (click it: query + chunks + latency)
   └─ generate_content     ← the cited answer
```

> 🔒 **Privacy:** to keep traces without prompt/response payloads, set `OPENINFERENCE_HIDE_INPUTS=true`
> and `OPENINFERENCE_HIDE_OUTPUTS=true`. *Trace structure always, payloads selectively.*
> Want a different backend? It's one env var — point `OTEL_EXPORTER_OTLP_ENDPOINT` at Langfuse or a
> Collector instead. (Concepts: [`observability_concepts.md`](observability_concepts.md).)

### Verify tracing is actually working

Three quick checks — useful to demo, and the first thing to run if traces don't show up:

**1. Is the Phoenix container up?**
```bash
docker compose ps phoenix   # STATUS should be "running"/"Up" (it starts with `make up`)
```

**2. Is the agent configured to export?** Confirm the env actually reached the container:
```bash
docker compose exec agent printenv OBSERVABILITY_ENABLED OTEL_EXPORTER_OTLP_ENDPOINT
# → OBSERVABILITY_ENABLED=true
#   OTEL_EXPORTER_OTLP_ENDPOINT=http://phoenix:6006/v1/traces
```
You can also check the agent's startup logs for the confirmation line from `observability.py`:
```bash
docker compose logs agent | grep -i "Observability enabled"
```

**3. Are traces arriving?** Open the UI ([http://localhost:6006](http://localhost:6006)) and ask a
question at `/dev-ui` — a new trace should appear under the project within a few seconds. No trace
after a request means the export isn't reaching Phoenix — recheck step 2, and make sure you didn't set
`OBSERVABILITY_ENABLED=false` in your `.env`.

### Inspect a trace in Phoenix

In the UI, click a trace and walk the tree (supervisor → `transfer_to_agent` → `search_article` →
synthesis). On each span, read:
- **the `search_article` (TOOL) span** — the `query` the model sent and the **chunks** it got back.
  This is where you catch *bad retrieval* (irrelevant chunks → likely bad answer).
- **the LLM (`call_llm`) spans** — **input/output tokens** (your cost driver) and **latency**.
- **the root span** — total end-to-end latency for the request.

That "open the worst trace and see where it broke" habit is exactly what online evaluation automates
over live **production** traffic — a managed Langfuse evaluator, set up in
[`gcp_deployment.md`](gcp_deployment.md) **Step 12**.

## 4. Run the evaluation suite

Install the host eval deps once (the agent image doesn't carry them). This creates a dedicated **uv**
virtualenv at `./.venv` and installs `./agent[eval]` into it — so you avoid `externally-managed-environment`
(PEP 668) and don't have to `activate` anything; the `make eval*` targets run that venv's Python directly:

```bash
make eval-deps     # uv venv .venv && uv pip install --python .venv "./agent[eval]"
```

> No uv? Install it once: `curl -LsSf https://astral.sh/uv/install.sh | sh` (or `brew install uv`).

> **Authenticate the judge first (`eval-judge` / `gate-judge` only).** The LLM
> judge runs on **Vertex AI** by default (`.env`: `GOOGLE_GENAI_USE_VERTEXAI=TRUE` +
> `GOOGLE_CLOUD_PROJECT`/`GOOGLE_CLOUD_LOCATION`), so it authenticates with your **gcloud
> Application Default Credentials**, not `GOOGLE_API_KEY`. Log in once (and re-run when the token
> expires — the symptom is `RefreshError: Reauthentication is needed`):
>
> ```bash
> gcloud auth application-default login
> ```
>
> No GCP access? Fall back to the AI Studio free tier instead: set `GOOGLE_GENAI_USE_VERTEXAI=FALSE`
> and a `GOOGLE_API_KEY` in `.env` (mind its ~20 req/day cap — one judge run is ~8 Gemini calls).
> The deterministic suite (`make eval` / `gate`) needs no judge auth at all.

The stack is already up (Step 1, `make up`), so just score the running system — each target reaches
the **real MCP sidecar + Qdrant** you started, not a stub, and tracing is on so the runs land in
Phoenix at <http://localhost:6006>:

```bash
make eval          # fast, deterministic (free criteria)
make eval-judge    # correctness via the Gemini judge — needs judge auth (see note above)
make gate          # deterministic regression gate    make gate-judge   # incl. judge datasets
```
*(raw equivalent of `make eval`, e.g. — note it runs the eval venv's Python explicitly:)*
```bash
MCP_SERVER_URL=http://localhost:3000/mcp .venv/bin/python -m pytest eval/test_fast.py -v
```

Full details — fixtures, the `.test.json` schema, the baseline gate — are in
[`../eval/README.md`](../eval/README.md).

## 5. Re-ingest on demand

If you've changed the corpus and want to reload the local Qdrant:

```bash
make ingest        # raw: docker compose run --rm ingestion
```

## 6. Tear down

```bash
make down          # stop containers, keep Qdrant + Postgres data
make nuke          # also wipe volumes (clean slate)
```

## Troubleshooting

**`make up` says ports are in use.** Something is already on 8080 / 6333 / 5432 / 3000. Stop it, or
change the host-side port in `docker-compose.yml`.

**Agent says it can't reach the knowledge base / `search_article` errors.** The agent reaches the
MCP server at `MCP_SERVER_URL`. In compose that's `http://mcp:3000/mcp` (the service name, not
`localhost`). If `mcp` failed to start, check `docker compose logs mcp` — usually a missing
`COHERE_API_KEY` (the MCP server does the embedding/search).

**First chat returns nothing / empty results.** The `ingestion` job hadn't finished populating
Qdrant before you asked. Check `docker compose logs ingestion` for a clean exit, then `make ingest`
to re-run it.

**Tracing: nothing shows in Phoenix.** (a) Check you didn't set `OBSERVABILITY_ENABLED=false` in your
`.env` — tracing is on by default, but that switch turns it off. (b) Confirm the Phoenix container is
running (`docker compose ps`) and the UI loads at <http://localhost:6006>. (c) Traces are batched —
give it a few seconds after the request.

**`make eval` can't reach the agent/MCP.** Run `make up` first; the eval runs on your host and needs
the MCP sidecar published at `localhost:3000`. Confirm with `curl -s localhost:3000/mcp` returning
something (even an error) rather than connection-refused.

**`LiteLLM:WARNING: ... could not pre-load bedrock-runtime ...`.** Cosmetic — LiteLLM probes cloud
providers we don't use. Already silenced in `agent/src/agent.py`.

**`PydanticSerializationError: ... LiteLLMClient`.** Known `google-adk` bug
([#5367](https://github.com/google/adk-python/issues/5367)); worked around in `agent/src/agent.py`
by subclassing `LiteLlm`. If you removed that block, restore it.
