"""Eval target — re-exports the *real* deployed agent so ADK's evaluator can import it.

`AgentEvaluator.evaluate(agent_module="research_assistant", ...)` imports this package and looks
for `research_assistant.agent.root_agent`. Rather than duplicate the agent, we put the real
`agent/` directory on the path and re-export its `root_agent`. This is the same supervisor +
kb_agent + web_search_agent you deploy — evaluated exactly as it ships.

Running this requires the agent's runtime env: COHERE_API_KEY and a reachable MCP Search server
over Streamable HTTP at `MCP_SERVER_URL` (which talks to Qdrant). Bring it up with
`docker compose up -d mcp qdrant`; the agent defaults to `http://localhost:3000/mcp`.
"""
import os
import sys

_AGENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "agent"))
if _AGENT_DIR not in sys.path:
    sys.path.insert(0, _AGENT_DIR)

from src.agent import root_agent  # noqa: E402  (path set up above)

__all__ = ["root_agent"]
