import os
import contextlib
import logging
from collections.abc import AsyncIterator

from mcp.server import Server
import mcp.types as types
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from starlette.applications import Starlette
from starlette.routing import Mount
from starlette.types import Receive, Scope, Send
import uvicorn

from src.embeddings.cohere_embedding import CohereMultimodalEmbedding
from src.vectordb.qdrant import QdrantSearch

logger = logging.getLogger(__name__)

# --- 1. Initialization ---
COHERE_API_KEY = os.environ.get("COHERE_API_KEY", "").strip()
embedding_model = CohereMultimodalEmbedding(api_key=COHERE_API_KEY)
search_engine = QdrantSearch(embedding_model=embedding_model, cohere_api_key=COHERE_API_KEY)

mcp_server = Server("Qdrant Search Server")

@mcp_server.list_tools()
async def list_tools() -> list[types.Tool]:
    return [
        types.Tool(
            name="search_article",
            description="Searches internal docs for AI trends and documentation.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 5}
                },
                "required": ["query"]
            }
        )
    ]

@mcp_server.call_tool()
async def call_tool(name: str, arguments: dict) -> list[types.TextContent]:
    if name == "search_article":
        query = arguments.get("query")
        limit = arguments.get("limit", 5)
        try:
            results = search_engine.search(query=query, limit=limit)
            return [types.TextContent(type="text", text=str(results))]
        except Exception as e:
            return [types.TextContent(type="text", text=f"Error: {str(e)}")]
    raise ValueError(f"Tool not found: {name}")

session_manager = StreamableHTTPSessionManager(
    app=mcp_server,
    event_store=None,
    stateless=True,
)

async def handle_streamable_http(scope: Scope, receive: Receive, send: Send) -> None:
    await session_manager.handle_request(scope, receive, send)

@contextlib.asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    """Context manager for session manager."""
    async with session_manager.run():
        logger.info("Application started with StreamableHTTP session manager!")
        try:
            yield
        finally:
            logger.info("Application shutting down...")

app = Starlette(
    debug=True,
    routes=[
        Mount("/mcp", app=handle_streamable_http),
    ],
    lifespan=lifespan,
)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "3000"))
    uvicorn.run(app, host="0.0.0.0", port=port)