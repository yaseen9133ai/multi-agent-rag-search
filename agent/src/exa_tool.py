import os
from exa_py import Exa
from google.adk.tools import FunctionTool

def exa_search(query: str, num_results: int = 5):
    """
    Searches the web using Exa AI for the most relevant and high-quality results.
    Exa is a search engine designed for LLMs, providing clean, parsed content.
    
    Args:
        query: The search query.
        num_results: Number of results to return (default 5).
    """
    api_key = os.environ.get("EXAAI_API_KEY")
    if not api_key:
        return {"error": "EXAAI_API_KEY not found in environment"}
    
    exa = Exa(api_key=api_key.strip())
    
    try:
        # We use use_autoprompt to let Exa optimize the query for search
        response = exa.search(
            query,
            num_results=num_results,
            type = "auto",
            contents = {"highlights": True}
        )

        formatted_results = []
        for result in response.results:
            # Safely extract fields to match the requested schema
            formatted_results.append({
                "id": getattr(result, "id", ""),
                "title": result.title or "Untitled",
                "url": result.url,
                "publishedDate": getattr(result, "published_date", None),
                "author": getattr(result, "author", None),
                "highlights": getattr(result, "highlights", []),
                "image": getattr(result, "image", None),
                "favicon": getattr(result, "favicon", None)
            })

        return {
            "requestId": getattr(response, "request_id", None),
            "results": formatted_results,
            "searchTime": getattr(response, "search_time", None)
        }
    except Exception as e:
        return {"error": f"Exa search failed: {str(e)}"}


exa_search_tool = FunctionTool(exa_search)
