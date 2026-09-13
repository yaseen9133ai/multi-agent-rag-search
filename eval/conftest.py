"""Shared pytest setup for the eval suite.

Makes the eval target importable and exposes skip-guards so the suite degrades cleanly when a
dependency is missing instead of erroring.

Transport note: the agent connects to the MCP Search server over **Streamable HTTP** (not a Stdio
subprocess), so the server must be running and reachable at `MCP_SERVER_URL` before eval. Bring it
up with `docker compose up -d mcp qdrant` (serves http://localhost:3000/mcp). `requires_mcp` skips
the suite when nothing is listening there, so a bare `pytest` doesn't hard-fail.
"""
import os
import socket
import sys
from urllib.parse import urlparse

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))

# eval/ on the path so `agent_module="research_assistant"` resolves to eval/research_assistant.
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

# Load deploy/.env (the Makefile doesn't), mainly for the judge's Vertex AI config. Env wins over file.
import _envload  # noqa: E402
_envload.load_dotenv()

# Register our custom metrics with ADK's default registry. AgentEvaluator.evaluate() reads
# custom_metrics from the config for the function path but does NOT register them, so without this
# the registry raises "NotFoundError: <name> not found in registry" at eval time.
import trajectory_metrics  # noqa: E402
trajectory_metrics.register()

# The agent reads MCP_SERVER_URL (default below). Use the Gemini API (not Vertex) for judges.
os.environ.setdefault("MCP_SERVER_URL", "http://localhost:3000/mcp")
os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "FALSE")

FIXTURES = os.path.join(_HERE, "fixtures")
FIXTURES_WEB = os.path.join(_HERE, "fixtures_web")
FIXTURES_JUDGE = os.path.join(_HERE, "fixtures_judge")


def _mcp_reachable() -> bool:
    """True if something is listening on the MCP_SERVER_URL host:port."""
    parsed = urlparse(os.environ["MCP_SERVER_URL"])
    host = parsed.hostname or "localhost"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=2):
            return True
    except OSError:
        return False


requires_cohere = pytest.mark.skipif(
    not os.environ.get("COHERE_API_KEY"),
    reason="COHERE_API_KEY not set — the agent model can't run.",
)

requires_mcp = pytest.mark.skipif(
    not _mcp_reachable(),
    reason=f"No MCP server reachable at {os.environ['MCP_SERVER_URL']} "
           "— run `docker compose up -d mcp qdrant` first.",
)

requires_exa = pytest.mark.skipif(
    not os.environ.get("EXAAI_API_KEY"),
    reason="EXAAI_API_KEY not set — the web_search_agent (Exa) can't run.",
)

requires_judge = pytest.mark.skipif(
    not os.environ.get("GOOGLE_API_KEY"),
    reason="GOOGLE_API_KEY not set — LLM-as-judge criteria need a Gemini key.",
)
