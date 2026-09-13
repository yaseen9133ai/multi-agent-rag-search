# LiteLLM pre-loads response-stream shapes for every cloud provider it
# supports (Bedrock, SageMaker, …) at import time. When the matching SDK
# isn't installed it logs a WARNING. We only use Cohere via LiteLLM, so
# those warnings are noise — bump LiteLLM's logger to ERROR to silence them.
# (Bedrock/SageMaker want `botocore`; we don't pull it in.)
import asyncio
import atexit
import logging
import os
import sys
from collections import deque

logging.getLogger("LiteLLM").setLevel(logging.ERROR)

# ─── Optional Cohere call counter (measure before you optimize) ─────────────────
# Set LLM_CALL_COUNT=1 to tally every LLM call this process makes (retries included)
# and print the total when it exits. Handy for seeing what a `make eval*` run costs
# against the Cohere trial key's 1000-calls/month cap — without guessing. Off by
# default so it never clutters production output. (Account-wide truth still lives at
# https://dashboard.cohere.com/ — this only counts calls from THIS process.)
_COUNT_CALLS = os.environ.get("LLM_CALL_COUNT", "").lower() not in ("", "0", "false", "no")
_llm_call_count = 0


def _print_llm_call_count() -> None:
    if _COUNT_CALLS:
        print(f"\n[LLM_CALL_COUNT] LLM calls this run: {_llm_call_count}", file=sys.stderr)


atexit.register(_print_llm_call_count)

from google.adk.agents import Agent
from google.adk.models.lite_llm import LiteLlm as _LiteLlm
from google.adk.tools.mcp_tool.mcp_toolset import MCPToolset
from google.adk.tools.mcp_tool.mcp_session_manager import StreamableHTTPConnectionParams
from .exa_tool import exa_search_tool

# ─── Optional client-side rate limit (for trial API keys) ──────────────────────
# Cohere *trial* keys cap at 20 requests/minute, and our eval suite fans out several
# agent runs concurrently (ADK's evaluator defaults to parallelism=4), each making
# multiple LLM calls. That bursts past the cap → HTTP 429, which LiteLLM surfaces as
# APIConnectionError (so its `num_retries` only does an immediate, wait-less retry —
# useless against a per-minute cap). A sliding-window limiter paces calls instead.
#
# Off by default (LLM_MAX_RPM unset/0) so production — on a paid key — runs full speed.
# The Makefile sets LLM_MAX_RPM for the eval targets. The limiter is a module-level
# singleton shared across all three agents, so the cap is global, not per-agent.
_LLM_MAX_RPM = int(os.environ.get("LLM_MAX_RPM", "0") or "0")


class _SlidingWindowRateLimiter:
    """Allow at most `max_calls` acquisitions per `period` seconds (async, shared)."""

    def __init__(self, max_calls: int, period: float = 60.0):
        self.max_calls = max_calls
        self.period = period
        self._calls: deque[float] = deque()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:  # serialize so the window is consistent under concurrency
            loop = asyncio.get_event_loop()
            while True:
                now = loop.time()
                while self._calls and now - self._calls[0] >= self.period:
                    self._calls.popleft()
                if len(self._calls) < self.max_calls:
                    self._calls.append(now)
                    return
                await asyncio.sleep(self.period - (now - self._calls[0]))


_rate_limiter = (
    _SlidingWindowRateLimiter(_LLM_MAX_RPM) if _LLM_MAX_RPM > 0 else None
)

# ─── Retry transient Cohere "empty completion" errors ──────────────────────────
# Every so often Cohere returns a completion with NEITHER text NOR a tool call.
# LiteLLM surfaces this as:
#     APIConnectionError: Cohere_chatException -
#         {"error_type":"NO_TOOL_CALL_OR_RESPONSE_GENERATED", ...}
# It's transient — the *same* request almost always succeeds on a second try — but
# it's fatal to a whole eval run: one empty completion bubbles up and fails the suite.
# So we retry it ourselves with a short backoff. (LiteLLM's own `num_retries` can't
# help: it retries instantly with no wait, and this isn't a rate-limit error to begin
# with — it's just flaky output.) We retry ONLY this specific error; everything else
# propagates unchanged.
_RETRYABLE_ERROR = "NO_TOOL_CALL_OR_RESPONSE_GENERATED"
_MAX_LLM_ATTEMPTS = 3  # 1 initial try + 2 retries

# ─── Workaround for google/adk-python#5367 ─────────────────────────────────────
class LiteLlm(_LiteLlm):
    def model_dump(self, *args, **kwargs):
        return {"model": self.model, "type": "LiteLlm"}

    async def generate_content_async(self, *args, **kwargs):
        for attempt in range(_MAX_LLM_ATTEMPTS):
            # Pace LLM calls when a rate limit is configured (no-op in production).
            # We re-acquire on every attempt so retries also respect the rate cap.
            if _rate_limiter is not None:
                await _rate_limiter.acquire()

            yielded = False  # did this attempt produce any output before failing?
            if _COUNT_CALLS:  # each attempt is one real provider call (counts even if it errors)
                global _llm_call_count
                _llm_call_count += 1
            try:
                async for response in super().generate_content_async(*args, **kwargs):
                    yielded = True
                    yield response
                return  # finished cleanly → stop retrying
            except Exception as error:
                last_attempt = attempt == _MAX_LLM_ATTEMPTS - 1
                # Give up (re-raise) unless this is the known transient Cohere error AND
                # it failed before yielding anything (so we never replay a half-streamed
                # response) AND we still have attempts left.
                if yielded or _RETRYABLE_ERROR not in str(error) or last_attempt:
                    raise
                # Brief, growing backoff before trying again: 0.5s, 1.0s, …
                await asyncio.sleep(0.5 * (attempt + 1))
# ───────────────────────────────────────────────────────────────────────────────

# 1. Knowledge Base Agent (MCP-based)
# Deliberately doesn't name a specific document/topic here — ingestion/files/ (and RETRIEVAL_MODE)
# can point at any PDF, so this description must stay accurate no matter what's been ingested.
kb_agent = Agent(
    name="kb_agent",
    model=LiteLlm(model="cohere_chat/command-a-03-2025"),
    description="An agent that specializes in answering questions based on the internal knowledge base (whatever documents have been ingested into it).",
    instruction=(
        "You are a specialized research assistant with access to the internal knowledge base. "
        "Use your MCP tools to look up relevant documents or internal data.\n\n"
        "If the retrieved results are relevant to the question: provide a detailed summary based "
        "ONLY on those retrieved facts, and cite sources as 'Internal Documentation' or specific "
        "document sections.\n\n"
        "If the search returns nothing relevant to the question: do NOT claim 'Internal "
        "Documentation' as a source for a non-answer, and do NOT tell the user you found nothing — "
        "transfer to `web_search_agent` instead, so the user still gets a real answer."
    ),
    tools=[
        MCPToolset(
            connection_params=StreamableHTTPConnectionParams(
                url=os.environ.get("MCP_SERVER_URL", "http://localhost:3000/mcp")
            )
        )
    ]
)

# 2. Web Search Agent (Exa Search)
# This agent handles general web searches using the Exa AI tool.
web_search_agent = Agent(
    name="web_search_agent",
    model=LiteLlm(model="cohere_chat/command-a-03-2025"),
    description="An agent that uses Exa Search to find the latest information from the web.",
    instruction=(
        "You are an expert web researcher. Use the Exa Search tool to find "
        "the latest information, news, or clarify topics not covered in the internal documentation. "
        "Focus on providing high-quality, up-to-date results from the open web."
    ),
    tools=[exa_search_tool]
)

# 3. General Assistant (Supervisor Agent)
# This agent orchestrates the conversation and delegates to specialists.
# Routing is deliberately topic-agnostic (checks the KB rather than keyword-matching a hardcoded
# document name) — the KB's actual contents change whenever ingestion/files/ or RETRIEVAL_MODE
# changes, so a hardcoded topic here would silently stop matching (as happened when the ingested
# PDF was swapped and kb_agent stopped being selected). But it must NOT check the KB for
# everything — ordinary world-knowledge questions should be answered directly, not routed to a
# specialist. Note also: once `transfer_to_agent` hands off to a specialist, THIS agent is no
# longer in the loop for that turn — kb_agent's own instruction handles its "found nothing" case
# by transferring onward to web_search_agent itself, since control doesn't return here.
GENERAL_ASSISTANT_INSTRUCTION = (
    "You are a General Assistant. Your goal is to provide comprehensive and helpful answers to user queries.\n\n"
    "Decide how to handle each question, in this order:\n"
    "1. If it's ordinary general knowledge you're confident about (world facts, definitions, basic "
    "trivia, chit-chat) with no obvious connection to a specialized document, answer DIRECTLY — "
    "do not delegate.\n"
    "2. Otherwise, if it could plausibly be answered by a specific, specialized, or niche document, "
    "call `kb_agent` — you don't know its exact topic in advance, so lean toward checking rather "
    "than assuming it isn't covered.\n"
    "3. Use `web_search_agent` directly for current events, recent news, or anything time-sensitive.\n\n"
    "Synthesize the final answer clearly."
)

root_agent = Agent(
    name="general_assistant",
    model=LiteLlm(model="cohere_chat/command-a-03-2025"),
    description="An assistant that answers user queries by delegating to specialized agents for internal knowledge and web search.",
    instruction=GENERAL_ASSISTANT_INSTRUCTION,
    sub_agents=[kb_agent, web_search_agent]
)
