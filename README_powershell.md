# Multi-Agent RAG + Web Search — Windows / PowerShell

> 🪟 **PowerShell edition of [`README.md`](README.md).** Same system, commands rewritten for Windows
> PowerShell. On macOS/Linux use [`README.md`](README.md). Step-by-step guides:
> [`docs/local_deployment_powershell.md`](docs/local_deployment_powershell.md) and
> [`docs/gcp_deployment_powershell.md`](docs/gcp_deployment_powershell.md).

A multi-agent **Agentic RAG** system (supervisor → knowledge-base agent → **MCP Search sidecar** over
Streamable HTTP → Qdrant, plus a web-search agent), deployable locally or on **Cloud Run**.

- ✅ **Observability** — ADK traces via **OpenTelemetry + OpenInference**: **Phoenix** for local dev
  and **Langfuse** for production (**required** for the Cloud Run deploy).
- ✅ **Evaluation** — a `pytest` suite in [`eval/`](eval/) scoring **trajectory** and **final response**.
- ✅ **A regression gate** — a baseline the suite compares against (`make gate`), so a change that
  makes the agent worse **fails the gate** when you run it. Wire the same command into CI to gate merges.
- ✅ **Bilingual RAG demo** — ships with a pre-ingested Arabic/English finance report so retrieval and
  citation work out of the box in both languages.

See [`README.md`](README.md) for the folder layout, the architecture, and the local↔cloud table —
they're identical; only the shell syntax below differs.

## Prerequisites

Same as [`README.md`](README.md): a billing-enabled GCP project + **gcloud CLI** (for the cloud
deploy only), **Docker Desktop**, a **Cohere** key, an **Exa** key, a **Qdrant Cloud** cluster (cloud
deploy), and optionally a **Gemini** key (LLM-judge eval) and a free **Langfuse** project (production
tracing + online eval).

Plus **[uv](https://docs.astral.sh/uv/)** to **run the eval suite** locally (`make eval-deps` builds an
isolated `.venv` with it). Install once: `irm https://astral.sh/uv/install.ps1 | iex` — see the
[install docs](https://docs.astral.sh/uv/getting-started/installation/). (Not needed for the deploy
itself; the Docker build carries its own uv.)

### Using `make` on Windows

The guides show every step as a raw `docker compose` / `gcloud` command **and** a `make` shortcut.
`make` is optional. If you want the shortcuts, install it once:

```powershell
winget install GnuWin32.Make
# or:  choco install make
```

Otherwise, just use the raw commands shown alongside each `make` target.

## Setup (one-time)

Copy the env template and fill in your values — **don't edit `.env.example` directly**:

```powershell
Copy-Item .env.example .env
# then edit .env and replace every placeholder
```

PowerShell doesn't have `source`. Load `.env` into the session with this loader (paste it whenever
you open a new terminal):

```powershell
Get-Content .env | Where-Object { $_ -match '^\s*[^#].+=' } | ForEach-Object {
    $name, $value = $_ -split '=', 2
    Set-Item -Path "Env:$($name.Trim())" -Value $value.Trim()
}
```

> 💡 **New terminal later?** Re-run the loader above — env vars don't persist across shells.

## Run it

### Locally with `docker-compose`

See **[`docs/local_deployment_powershell.md`](docs/local_deployment_powershell.md)** — bring the
stack up, talk to it at `/dev-ui`, turn on local **Phoenix** tracing, run the **eval suite**, tear down.

### On Google Cloud Run

See **[`docs/gcp_deployment_powershell.md`](docs/gcp_deployment_powershell.md)** — every `gcloud`
command in PowerShell: APIs, Cloud SQL, secrets, IAM, build, ingest, deploy the two-container
service, production tracing, and online evaluation.

## Observability & evaluation at a glance

- **Tracing** is one env toggle (`OBSERVABILITY_ENABLED` + an OTLP endpoint). **Local dev → Phoenix**:
  **on by default** — `make up` starts Phoenix as a compose service and the agent exports to it (set
  `OBSERVABILITY_ENABLED=false` to opt out). **Production → Langfuse** (deploy guide Step 11). Same
  agent code; only the endpoint changes.
- **Evaluation** lives in [`eval/`](eval/) — see [`eval/README.md`](eval/README.md). With the stack
  up: `make eval` / `make eval-judge` / `make gate` (or the raw `pytest` / `python` commands the
  local guide lists). Wire the same commands into CI (e.g. GitHub Actions) to run them automatically.

> New to the telemetry vocabulary (OTel, OTLP, OpenInference)? See
> [`docs/observability_concepts.md`](docs/observability_concepts.md).
