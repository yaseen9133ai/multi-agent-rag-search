import os
from typing import List, Dict, Any, Optional
from qdrant_client import QdrantClient
import cohere
from src.vectordb.base import BaseVectorDB
from src.embeddings.base import BaseEmbedding

class QdrantSearch(BaseVectorDB):
    """
    Qdrant implementation for Vector Search with Cohere Reranking.
    Follows best practices for dependency injection and environment configuration.
    """

    def __init__(
        self,
        embedding_model: BaseEmbedding,
        collection_name: Optional[str] = None,
        cohere_api_key: Optional[str] = None,
        rerank_model: Optional[str] = None,
        qdrant_url: Optional[str] = None,
        qdrant_api_key: Optional[str] = None,
        qdrant_host: Optional[str] = None,
        qdrant_port: Optional[int] = None,
    ):
        """
        Initializes the QdrantSearch class.
        
        Args:
            embedding_model: An instance of a class inheriting from BaseEmbedding.
            collection_name: The name of the Qdrant collection.
            cohere_api_key: API key for Cohere (used for reranking).
            rerank_model: The Cohere rerank model to use.
            qdrant_url: URL for Qdrant Cloud.
            qdrant_api_key: API key for Qdrant Cloud.
            qdrant_host: Host for local Qdrant.
            qdrant_port: Port for local Qdrant.
        """
        self.embedding_model = embedding_model

        # Configuration from arguments or environment variables
        # "visual" (default) — dense-only search over page-image vectors.
        # "hybrid" — dense + BM25 sparse fused with RRF, over text-chunk vectors.
        # Collection is name-suffixed by mode to match ingestion's naming (see ingestion/main.py) —
        # each mode has its own collection, so switching modes never mismatches the vector schema.
        self.retrieval_mode = os.environ.get("RETRIEVAL_MODE", "visual").strip().lower()
        base_collection_name = collection_name or os.environ.get("QDRANT_COLLECTION", "documents")
        self.collection_name = f"{base_collection_name}_{self.retrieval_mode}"
        self.rerank_model = rerank_model or os.environ.get("COHERE_RERANK_MODEL", "rerank-v3.5")

        # BM25 sparse model only needed (and only loaded) for hybrid mode.
        self.bm25 = None
        if self.retrieval_mode == "hybrid":
            from fastembed import SparseTextEmbedding
            self.bm25 = SparseTextEmbedding("Qdrant/bm25")
        
        # Initialize Cohere client for reranking
        # Following the pattern from CohereMultimodalEmbedding of requiring an API key
        api_key = cohere_api_key or os.environ.get("COHERE_API_KEY", "").strip()
        if not api_key:
            raise ValueError("COHERE_API_KEY must be provided or set in environment variables")
        self.cohere_client = cohere.ClientV2(api_key=api_key)

        # Initialize Qdrant client
        # Support both Cloud and Local modes based on environment or parameters
        q_mode = os.environ.get("QDRANT_MODE", "local").lower()
        q_url = qdrant_url or os.environ.get("QDRANT_URL", "").strip()
        q_api_key = qdrant_api_key or os.environ.get("QDRANT_API_KEY", "").strip()

        if q_mode == "cloud" or (q_url and q_api_key):
            self.qdrant_client = QdrantClient(
                url=q_url,
                api_key=q_api_key,
            )
        else:
            host = qdrant_host or os.environ.get("QDRANT_HOST", "qdrant")
            port = int(qdrant_port or os.environ.get("QDRANT_PORT", "6333"))
            self.qdrant_client = QdrantClient(host=host, port=port)

    def _embed_query_dense(self, query: str) -> List[float]:
        if hasattr(self.embedding_model, "embed_text_query"):
            return self.embedding_model.embed_text_query(query)
        return self.embedding_model.embed_text(query)

    def _search_visual(self, query: str, limit: int) -> List[Any]:
        """Dense-only search over page-image vectors (the default)."""
        query_vector = self._embed_query_dense(query)
        search_response = self.qdrant_client.query_points(
            collection_name=self.collection_name,
            query=query_vector,
            limit=limit,
        )
        return search_response.points

    def _search_hybrid(self, query: str, limit: int) -> List[Any]:
        """Dense + BM25 sparse fused with RRF, over text-chunk vectors."""
        from qdrant_client.models import FusionQuery, Prefetch, SparseVector

        dense_vector = self._embed_query_dense(query)
        sv = next(iter(self.bm25.query_embed([query])))
        sparse_vector = SparseVector(indices=sv.indices.tolist(), values=sv.values.tolist())

        search_response = self.qdrant_client.query_points(
            collection_name=self.collection_name,
            prefetch=[
                Prefetch(query=dense_vector, using="dense", limit=limit),
                Prefetch(query=sparse_vector, using="sparse", limit=limit),
            ],
            query=FusionQuery(fusion="rrf"),
            limit=limit,
        )
        return search_response.points

    def search(self, query: str, limit: int = 10, rerank_top_n: int = 3) -> List[Dict[str, Any]]:
        """
        Single search method that:
        1. Retrieves candidates from Qdrant — dense-only ("visual" mode) or dense+sparse RRF
           ("hybrid" mode), per self.retrieval_mode.
        2. Reranks results using Cohere.

        Args:
            query: The natural-language search query.
            limit: Number of candidates to retrieve from Qdrant before reranking.
            rerank_top_n: Number of final results to return after reranking.

        Returns:
            List of dictionaries with 'text', 'score', and 'metadata'.
        """
        # 1. Retrieve candidates (mode-dependent)
        if self.retrieval_mode == "hybrid":
            candidates = self._search_hybrid(query, limit)
        else:
            candidates = self._search_visual(query, limit)

        if not candidates:
            return []

        # 2. Rerank using Cohere
        documents = [c.payload.get("text", "") for c in candidates if c.payload]
        
        rerank_results = self.cohere_client.rerank(
            model=self.rerank_model,
            query=query,
            documents=documents,
            top_n=rerank_top_n,
        )

        # 3. Format final results
        final_results = []
        for r in rerank_results.results:
            # Match rerank index back to original candidate
            candidate = candidates[r.index]
            final_results.append({
                "text": candidate.payload.get("text", ""),
                "score": r.relevance_score,
                "metadata": candidate.payload
            })

        return final_results
