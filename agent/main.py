"""FastAPI entrypoint for the multi-agent assistant.

ADK ships a helper, `google.adk.cli.fast_api.get_fast_api_app`, that builds a
FastAPI app exposing:

  • the agent runner endpoints (`/run`, `/run_sse`, `/list-apps`, …)
  • the ADK web UI at `/dev-ui`
  • session persistence via the URL passed in `session_service_uri`

`AGENT_DIR` is the parent of this file, so ADK discovers `src.agent.root_agent`
and the agent is reachable as `src/` in the API.

Sessions URL — two paths, one helper:

  • Local docker-compose: `SESSIONS_DB_URL` is set via `env_file: .env`
    and the `environment:` block in `docker-compose.yml`. We use it as-is.
  • Cloud Run: `SESSIONS_DB_URL` is NOT set on deploy. We read `DB_PASS`
    (wired in by `--set-secrets`) and `CLOUDSQL_INSTANCE` (an env var) and
    build the URL pointing at the Cloud SQL Auth Proxy socket. The password
    is URL-encoded with `quote_plus` because `openssl rand -base64 24`
    routinely produces `/`, `+`, `=` — all reserved in URLs.
"""

import os
from urllib.parse import quote_plus

from fastapi import FastAPI
from google.adk.cli.fast_api import get_fast_api_app

from src.observability import init_observability

AGENT_DIR = os.path.dirname(os.path.abspath(__file__))

# Instrument ADK → Phoenix (local) or Langfuse (prod). No-op unless OBSERVABILITY_ENABLED=true.
# Runs before the app/agent is built so the instrumentation patches ADK's Runner before any request.
init_observability()

# --- SANITIZATION ---
# Critical: Strip trailing newlines/whitespace from secrets. 
# PowerShell pipes and some editors often append a \n which causes 
# "header injection" errors in Cohere/LiteLLM.
for key in [
    "COHERE_API_KEY", "QDRANT_API_KEY", "QDRANT_URL", 
    "EXAAI_API_KEY", "DB_PASS", "CLOUDSQL_INSTANCE"
]:
    if val := os.environ.get(key):
        os.environ[key] = val.strip()


def _sessions_uri() -> str:
    """SQLAlchemy URL for ADK's `DatabaseSessionService`."""
    if url := os.environ.get("SESSIONS_DB_URL"):
        return url.strip()
    # Cloud Run path — build from the DB_PASS secret + Cloud SQL instance name.
    db_user = os.environ.get("DB_USER", "app")
    db_pass = quote_plus(os.environ["DB_PASS"].strip())
    instance = os.environ["CLOUDSQL_INSTANCE"]  # e.g. project:region:agent-sessions
    return f"postgresql+asyncpg://{db_user}:{db_pass}@/sessions?host=/cloudsql/{instance}"


app: FastAPI = get_fast_api_app(
    agents_dir=AGENT_DIR,
    web=True,
    session_service_uri=_sessions_uri(),
)
app.title = "multi-agent-rag-search"
app.description = "Agentic RAG with fallback to web search. Powered by ADK and Cohere."


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))