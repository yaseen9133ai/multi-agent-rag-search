"""Fast, deterministic eval — the cheap gate you run on every change.

(Wire this exact command into CI — e.g. GitHub Actions — to run it on every push.)

No LLM judge, no cloud, no cost beyond running the agent itself. This is the suite your CI gates on
for every commit. It checks the **process**, not answer quality:

  • `delegated_and_retrieved` (a custom metric — see trajectory_metrics.py): did the supervisor
    delegate to `kb_agent` and call `search_article`? It checks tool NAMES only.

Why not the prebuilt `tool_trajectory_avg_score` / `response_match_score`? Both are too brittle for a
*generative* agent: trajectory scoring compares tool **arguments**, but our `search_article` query is
model-generated and varies every run; and `response_match_score` (ROUGE) rarely clears a useful
threshold against one free-form reference. So we assert the stable path here and push **answer
quality** (groundedness, semantic match) to the judge suite — `make evaluate-judge` / test_judge.py.

Requires the agent's runtime deps because it must actually run to produce a trajectory:
COHERE_API_KEY (agent model) and a reachable MCP Search server over HTTP (which talks to Qdrant).
Bring the server up with `docker compose up -d mcp qdrant`.
"""
import pytest
from google.adk.evaluation.agent_evaluator import AgentEvaluator

from conftest import FIXTURES, FIXTURES_WEB, requires_cohere, requires_mcp, requires_exa


@requires_cohere
@requires_mcp
@pytest.mark.asyncio
async def test_kb_search_delegates_and_retrieves():
    """KB branch: the supervisor must delegate to kb_agent and call search_article before answering."""
    await AgentEvaluator.evaluate(
        agent_module="research_assistant",
        eval_dataset_file_path_or_dir=f"{FIXTURES}/kb_search.test.json",
        num_runs=1,  # 1 run keeps Cohere trial-key usage down; the trajectory check is stable enough.
    )


@requires_cohere
@requires_exa
@pytest.mark.asyncio
async def test_web_search_routes_and_searches():
    """Web branch: a 'latest news' question must route to web_search_agent and call exa_search.

    This makes a LIVE Exa call, so it's gated on EXAAI_API_KEY and runs once. We assert only the
    *route* (the `routed_to_web_search` custom metric) — not the answer text, because live web content
    changes run to run. Answer faithfulness to the fetched results is the judge's job (groundedness).
    """
    await AgentEvaluator.evaluate(
        agent_module="research_assistant",
        eval_dataset_file_path_or_dir=f"{FIXTURES_WEB}/web_search.test.json",
        num_runs=1,
    )
