"""Text chunking and embedding for the school knowledge base.

Splits extracted document text into overlapping chunks, embeds them with
OpenAI's embedding model, and stores the vectors in pgvector for retrieval.
"""

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import Document, DocumentChunk
from app.services.ai import client
from app.services.pricing import embedding_cost_cents

CHUNK_SIZE = 800
CHUNK_OVERLAP = 100
MAX_CHUNKS_PER_DOC = 200
BATCH_SIZE = 50


def chunk_text(text: str) -> list[str]:
    """Split text into overlapping chunks by character count."""
    text = text.strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = start + CHUNK_SIZE
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - CHUNK_OVERLAP
        if len(chunks) >= MAX_CHUNKS_PER_DOC:
            break
    return chunks


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Call OpenAI embeddings API. Returns one vector per input text."""
    result = await client.embeddings.create(
        model=settings.embedding_model,
        input=texts,
    )
    return [item.embedding for item in result.data]


async def embed_document(db: AsyncSession, doc: Document, text: str) -> int:
    """Chunk, embed and store a document. Returns the number of chunks created."""
    chunks = chunk_text(text)
    if not chunks:
        doc.status = "empty"
        doc.chunk_count = 0
        await db.flush()
        return 0

    total_stored = 0
    for i in range(0, len(chunks), BATCH_SIZE):
        batch = chunks[i : i + BATCH_SIZE]
        vectors = await embed_texts(batch)
        for j, (chunk_text_item, vector) in enumerate(zip(batch, vectors)):
            db.add(
                DocumentChunk(
                    document_id=doc.id,
                    ordinal=i + j,
                    content=chunk_text_item,
                    embedding=vector,
                )
            )
        total_stored += len(batch)

    doc.status = "ready"
    doc.chunk_count = total_stored
    await db.flush()
    return total_stored


async def search_similar(
    db: AsyncSession, query: str, *, limit: int = 5, min_score: float = 0.3
) -> list[dict]:
    """Find the most relevant document chunks for a query."""
    query_vectors = await embed_texts([query])
    query_vec = query_vectors[0]

    stmt = (
        select(
            DocumentChunk.content,
            DocumentChunk.document_id,
            DocumentChunk.embedding.cosine_distance(query_vec).label("distance"),
        )
        .join(Document, Document.id == DocumentChunk.document_id)
        .where(Document.status == "ready")
        .order_by("distance")
        .limit(limit)
    )
    rows = (await db.execute(stmt)).all()

    results = []
    for content, doc_id, distance in rows:
        score = 1.0 - distance
        if score < min_score:
            continue
        doc = await db.get(Document, doc_id)
        results.append({
            "content": content,
            "document_id": str(doc_id),
            "filename": doc.filename if doc else "unknown",
            "score": round(score, 3),
        })
    return results
