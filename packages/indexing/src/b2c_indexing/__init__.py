"""L3 source-aware chunking and retrieval baselines."""
from .chunking import chunk_book, estimate_tokens
from .retrieval import dense_search, lexical_search, reciprocal_rank_fusion
from .postgres_store import PostgresChunkStore
from .query_service import QueryRetrievalService, RetrievalHit
from .embedding_client import OpenAIEmbeddingClient

__all__ = [
    "chunk_book", "estimate_tokens", "dense_search", "lexical_search",
    "reciprocal_rank_fusion", "PostgresChunkStore", "QueryRetrievalService",
    "RetrievalHit", "OpenAIEmbeddingClient",
]
