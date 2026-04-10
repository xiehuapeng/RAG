from __future__ import annotations

from typing import Any

from app.services.langchain_runtime import (
    chroma_delete,
    chroma_query,
    chroma_upsert,
    get_embeddings,
    get_vector_store,
)


def get_embedding_function():
    """兼容旧调用方的保留函数。

    现在底层已经改成 LangChain `Embeddings` 对象，这里继续保留原方法名，
    避免其他模块导入路径变动过大。
    """

    return get_embeddings()


def get_collection():
    """兼容旧调用方的保留函数。"""

    return get_vector_store()


def upsert_chunks(ids: list[str], documents: list[str], metadatas: list[dict[str, Any]]) -> None:
    chroma_upsert(ids, documents, metadatas)


def delete_chunks(ids: list[str]) -> None:
    chroma_delete(ids)


def query_chunks(query: str, top_k: int) -> list[tuple[Any, float]]:
    """向上层返回 LangChain `similarity_search_with_score` 结果。"""

    return chroma_query(query, top_k)
