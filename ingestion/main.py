import json
import os
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv
from qdrant_client import QdrantClient
from qdrant_client.http import models
from safetensors.numpy import load_file

# Load environment variables
load_dotenv()

QDRANT_URL = os.getenv("QDRANT_URL", "").strip()
QDRANT_API_KEY = os.getenv("QDRANT_API_KEY", "").strip()
QDRANT_COLLECTION_BASE = os.getenv("QDRANT_COLLECTION", "documents").strip()

# "visual"  — page-image multimodal embeddings + pre-baked Mistral OCR text (the default).
# "hybrid"  — chunk the PDF text, embed dense (Cohere) + sparse (BM25), RRF at query time.
# Each mode gets its own collection (name-suffixed) since the two need different vector schemas —
# switching modes never clashes with or overwrites the other mode's data.
RETRIEVAL_MODE = os.getenv("RETRIEVAL_MODE", "visual").strip().lower()
QDRANT_COLLECTION = f"{QDRANT_COLLECTION_BASE}_{RETRIEVAL_MODE}"

FILES_DIR = Path("ingestion/files")
# hybrid mode only — the PDF it extracts text from. visual mode ignores this: it only reads the
# pre-baked JSONL_PATH/SAFETENSORS_PATH below, whatever their source PDF was named.
PDF_FILENAME = os.getenv("INGEST_PDF_FILENAME", "article.pdf").strip()
PDF_PATH = FILES_DIR / PDF_FILENAME

# visual mode only — basename (no extension) of the pre-baked <name>.jsonl / <name>.safetensors
# pair in FILES_DIR. Defaults to the PDF basename so a single INGEST_PDF_FILENAME setting covers
# both modes; override with INGEST_DOC_BASENAME if the baked files use a different name.
DOC_BASENAME = os.getenv("INGEST_DOC_BASENAME", "").strip() or Path(PDF_FILENAME).stem
JSONL_PATH = FILES_DIR / f"{DOC_BASENAME}.jsonl"
SAFETENSORS_PATH = FILES_DIR / f"{DOC_BASENAME}.safetensors"

# Recursive splitter: paragraph -> line -> sentence -> word fallback, with overlap so facts
# straddling a chunk boundary still land in at least one chunk.
CHUNK_SIZE = 512
CHUNK_OVERLAP = 100

# Cohere's embed endpoint hard-caps a single request at 96 texts. A short PDF may never chunk
# past that, but a long document can chunk into hundreds of pieces, so hybrid mode batches.
COHERE_EMBED_BATCH_SIZE = 96


def _build_client() -> QdrantClient:
    api_key = QDRANT_API_KEY if QDRANT_API_KEY else None
    return QdrantClient(url=QDRANT_URL, api_key=api_key)


def ingest_visual():
    """Page-image multimodal embeddings + pre-baked Mistral OCR text."""
    print(f"Starting VISUAL ingestion for {JSONL_PATH.name} -> collection '{QDRANT_COLLECTION}'...")

    client = _build_client()

    # Ensure collection exists
    collections = client.get_collections().collections
    exists = any(c.name == QDRANT_COLLECTION for c in collections)

    if not exists:
        print(f"Creating collection {QDRANT_COLLECTION}...")
        client.create_collection(
            collection_name=QDRANT_COLLECTION,
            vectors_config=models.VectorParams(size=1536, distance=models.Distance.COSINE),
        )

    # Load pre-baked page-image embeddings
    print("Loading embeddings...")
    embeddings_dict = load_file(str(SAFETENSORS_PATH))
    embeddings = embeddings_dict["embeddings"]
    print(f"Loaded embeddings with shape: {embeddings.shape}")

    # Load and process pre-baked OCR data
    print("Processing OCR data...")
    pages_data = []

    with open(JSONL_PATH, "r", encoding="utf-8") as f:
        for i, line in enumerate(f):
            page_elements = json.loads(line)
            page_number = i + 1

            # Combine all text content from page elements
            text_contents = [
                el["content"] for el in page_elements
                if el.get("content") is not None
            ]
            combined_text = "\n\n".join(text_contents).strip()

            # We subtract 1 from page_number because embeddings are 0-indexed
            # and page numbers start at 1
            embedding = embeddings[page_number - 1]

            pages_data.append({
                "page_number": page_number,
                "text": combined_text,
                "embedding": embedding.tolist()
            })

    print(f"Prepared {len(pages_data)} pages for upload.")

    # Upload to Qdrant
    print("Uploading to Qdrant...")
    file_name = PDF_FILENAME  # Use the original PDF name for metadata
    processed_at = datetime.utcnow().isoformat()

    ids = [i for i in range(len(pages_data))]
    payloads = [
        {
            "file_name": file_name,
            "page_number": p["page_number"],
            "text": p["text"],
            "processed_at": processed_at
        }
        for p in pages_data
    ]
    vectors = [p["embedding"] for p in pages_data]

    client.upload_collection(
        collection_name=QDRANT_COLLECTION,
        vectors=vectors,
        payload=payloads,
        ids=ids,
        parallel=4
    )

    print("Visual ingestion completed successfully!")


def ingest_hybrid():
    """Chunked text, dense (Cohere) + sparse (BM25) vectors, run live off the PDF."""
    import cohere
    import pymupdf4llm
    from fastembed import SparseTextEmbedding
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from qdrant_client.models import (
        Distance,
        Modifier,
        PointStruct,
        SparseVector,
        SparseVectorParams,
        VectorParams,
    )

    print(f"Starting HYBRID ingestion for {PDF_PATH.name} -> collection '{QDRANT_COLLECTION}'...")

    cohere_api_key = os.getenv("COHERE_API_KEY", "").strip()
    if not cohere_api_key:
        raise ValueError("COHERE_API_KEY must be set for RETRIEVAL_MODE=hybrid")
    co = cohere.ClientV2(api_key=cohere_api_key)

    client = _build_client()

    print("Extracting text from PDF (pymupdf4llm)...")
    md_text = pymupdf4llm.to_markdown(str(PDF_PATH))

    print("Chunking text...")
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=CHUNK_SIZE,
        chunk_overlap=CHUNK_OVERLAP,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    texts = splitter.split_text(md_text)
    print(f"  -> {len(texts)} chunks")

    print("Computing dense embeddings (Cohere embed-v4.0)...")
    dense_vectors = []
    for i in range(0, len(texts), COHERE_EMBED_BATCH_SIZE):
        batch = texts[i:i + COHERE_EMBED_BATCH_SIZE]
        response = co.embed(
            model="embed-v4.0",
            input_type="search_document",
            embedding_types=["float"],
            texts=batch,
        )
        dense_vectors.extend(response.embeddings.float)
        print(f"  -> embedded {len(dense_vectors)}/{len(texts)} chunks")

    print("Computing sparse vectors (BM25 via fastembed)...")
    bm25 = SparseTextEmbedding("Qdrant/bm25")
    sparse_vectors = [
        SparseVector(indices=sv.indices.tolist(), values=sv.values.tolist())
        for sv in bm25.embed(texts)
    ]

    print(f"(Re)creating collection '{QDRANT_COLLECTION}'...")
    client.recreate_collection(
        collection_name=QDRANT_COLLECTION,
        vectors_config={
            "dense": VectorParams(size=len(dense_vectors[0]), distance=Distance.COSINE),
        },
        sparse_vectors_config={
            "sparse": SparseVectorParams(modifier=Modifier.IDF),
        },
    )

    print("Upserting points...")
    file_name = PDF_PATH.name
    processed_at = datetime.utcnow().isoformat()

    client.upsert(
        collection_name=QDRANT_COLLECTION,
        points=[
            PointStruct(
                id=i,
                vector={"dense": dense_vectors[i], "sparse": sparse_vectors[i]},
                payload={"text": texts[i], "file_name": file_name, "processed_at": processed_at},
            )
            for i in range(len(texts))
        ],
    )

    print(f"Hybrid ingestion completed successfully! Collection has "
          f"{client.get_collection(QDRANT_COLLECTION).points_count} points.")


def ingest():
    if RETRIEVAL_MODE == "hybrid":
        ingest_hybrid()
    elif RETRIEVAL_MODE == "visual":
        ingest_visual()
    else:
        raise ValueError(
            f"Unknown RETRIEVAL_MODE '{RETRIEVAL_MODE}' — expected 'visual' or 'hybrid'."
        )


if __name__ == "__main__":
    ingest()
