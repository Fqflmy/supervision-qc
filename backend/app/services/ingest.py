# -*- coding: utf-8 -*-
"""入库服务：解析 → 切分 → 向量化 → 索引（SRS FR-KB-03~06, FR-KB-10）。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import ChunkStatus, DocStatus
from app.core.errors import AppError
from app.core.logging_conf import get_logger
from app.db.models import DocChunk, DocVersion, SpecDoc
from app.ingest.parser import ParsedDocument, parse_document
from app.ingest.splitter import Chunk, split_blocks
from app.retrieval.bm25 import Bm25Document, get_bm25_index
from app.retrieval.embedding import embed_texts, get_embedder
from app.retrieval.vector_store import get_faiss_index
from app.services.storage import get_storage

logger = get_logger(__name__)

DEFAULT_NAMESPACE = "default"


class IngestError(AppError):
    code_key = "INTERNAL_ERROR"
    http_status = 500
    message = "文档入库失败"


@dataclass
class IngestResult:
    doc_id: int
    version_id: int
    chunk_count: int
    indexed: int
    page_count: int
    char_count: int
    parse_meta: dict
    namespace: str
    degraded_embedding: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "version_id": self.version_id,
            "chunk_count": self.chunk_count,
            "indexed": self.indexed,
            "page_count": self.page_count,
            "char_count": self.char_count,
            "parse_meta": self.parse_meta,
            "namespace": self.namespace,
            "degraded_embedding": self.degraded_embedding,
        }


def namespace_of(doc: SpecDoc) -> str:
    return f"kb_{doc.kb_id}" if doc.kb_id else DEFAULT_NAMESPACE


async def ingest_document(
    session: Session,
    version: DocVersion,
    doc: SpecDoc,
    *,
    force: bool = False,
    namespace: Optional[str] = None,
) -> IngestResult:
    """解析并索引一个文档版本。"""
    namespace = namespace or namespace_of(doc)
    storage = get_storage()
    path = storage.path_of(version.file_path)
    if not path.exists():
        raise IngestError(f"原始文件不存在：{version.file_path}")

    version.parse_status = "parsing"
    version.parse_error = None
    session.flush()

    try:
        parsed: ParsedDocument = await asyncio.to_thread(parse_document, path)
    except Exception as exc:  # noqa: BLE001
        version.parse_status = "failed"
        version.parse_error = str(exc)[:1000]
        doc.status = DocStatus.FAILED.value
        session.flush()
        logger.error("文档解析失败", extra={"doc_id": doc.id, "error": str(exc)[:300]})
        raise

    chunks = split_blocks(parsed.blocks)
    if not chunks:
        version.parse_status = "failed"
        version.parse_error = "切分结果为空"
        doc.status = DocStatus.FAILED.value
        session.flush()
        raise IngestError("切分结果为空，请检查文档内容")

    if force:
        removed = _remove_existing(session, version, namespace)
        logger.info("强制重新解析，已清理旧分块", extra={"version_id": version.id, "removed": removed})

    records = await _persist_chunks(session, version, doc, chunks)
    indexed = await _index_chunks(records, namespace)

    version.parse_status = "parsed"
    version.chunk_count = len(records)
    version.page_count = parsed.page_count or None
    version.file_hash = parsed.file_hash or version.file_hash
    version.file_type = parsed.file_type or version.file_type
    version.parse_meta = {
        "blocks": len(parsed.blocks),
        "chunks": len(chunks),
        "char_count": parsed.char_count,
        "tables": sum(1 for c in chunks if c.is_table),
        "embedding_model": get_embedder().name,
        "degraded_embedding": get_embedder().degraded,
    }
    if doc.status in (DocStatus.DRAFT.value, DocStatus.FAILED.value, DocStatus.PARSING.value):
        doc.status = DocStatus.PENDING_REVIEW.value
    session.flush()

    logger.info(
        "文档入库完成",
        extra={
            "doc_id": doc.id,
            "version_id": version.id,
            "chunks": len(records),
            "indexed": indexed,
            "namespace": namespace,
        },
    )
    return IngestResult(
        doc_id=doc.id,
        version_id=version.id,
        chunk_count=len(records),
        indexed=indexed,
        page_count=parsed.page_count,
        char_count=parsed.char_count,
        parse_meta=version.parse_meta or {},
        namespace=namespace,
        degraded_embedding=get_embedder().degraded,
    )


def _remove_existing(session: Session, version: DocVersion, namespace: str) -> int:
    existing = session.execute(
        select(DocChunk.id).where(DocChunk.doc_version_id == version.id)
    ).scalars().all()
    if not existing:
        return 0
    ids = [int(i) for i in existing]
    get_faiss_index(namespace).remove(ids)
    get_bm25_index(namespace).remove(ids)
    session.query(DocChunk).filter(DocChunk.doc_version_id == version.id).delete()
    session.flush()
    return len(ids)


async def _persist_chunks(
    session: Session, version: DocVersion, doc: SpecDoc, chunks: Sequence[Chunk]
) -> list[DocChunk]:
    records: list[DocChunk] = []
    for chunk in chunks:
        record = DocChunk(
            doc_version_id=version.id,
            doc_id=doc.id,
            clause_no=chunk.clause_no,
            chapter_path=chunk.chapter_path,
            chunk_index=chunk.chunk_index,
            page_no=chunk.page_no,
            content=chunk.content,
            token_count=chunk.token_count,
            embedding_model=get_embedder().name,
            status=ChunkStatus.ACTIVE.value,
            extra={"is_table": chunk.is_table, "child_clause_nos": chunk.child_clause_nos},
        )
        session.add(record)
        records.append(record)
    session.flush()
    return records


async def _index_chunks(records: Sequence[DocChunk], namespace: str) -> int:
    """写入 FAISS 与 BM25，并持久化索引文件。"""
    if not records:
        return 0
    texts = [r.content for r in records]
    ids = [int(r.id) for r in records]

    # 向量化（分批，避免一次性占用过多内存）
    batch = max(1, settings.embedding_batch_size)
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch):
        vectors.extend(await embed_texts(texts[start : start + batch], is_query=False))

    faiss_index = get_faiss_index(namespace)
    faiss_index.add(
        vectors,
        ids,
        [
            {"clause_no": r.clause_no, "doc_id": int(r.doc_id), "version_id": int(r.doc_version_id)}
            for r in records
        ],
    )
    for record, vector_id in zip(records, ids):
        record.faiss_id = vector_id
    faiss_index.save()

    bm25 = get_bm25_index(namespace)
    bm25.add(
        [
            Bm25Document(
                chunk_id=int(r.id),
                text=r.content,
                clause_no=r.clause_no,
                doc_id=int(r.doc_id),
                meta={"chapter_path": r.chapter_path, "page_no": r.page_no},
            )
            for r in records
        ]
    )
    bm25.save()
    return len(records)


async def rebuild_index(
    session: Session, *, namespace: Optional[str] = None, only_published: bool = True
) -> dict[str, Any]:
    """全量重建索引（FR-KB-10 / 运维重建）。"""
    stmt = (
        select(DocChunk, SpecDoc)
        .join(SpecDoc, DocChunk.doc_id == SpecDoc.id)
        .where(DocChunk.status == ChunkStatus.ACTIVE.value)
        .order_by(DocChunk.id)
    )
    if only_published:
        stmt = stmt.where(SpecDoc.status == DocStatus.PUBLISHED.value)
    rows = session.execute(stmt).all()
    if not rows:
        return {"chunks": 0, "message": "没有可分块可索引"}

    namespace = namespace or DEFAULT_NAMESPACE
    get_faiss_index(namespace).remove([int(c.id) for c, _ in rows])
    get_bm25_index(namespace).remove([int(c.id) for c, _ in rows])

    records = [chunk for chunk, _doc in rows]
    indexed = await _index_chunks(records, namespace)
    return {"chunks": len(rows), "indexed": indexed, "namespace": namespace}


def pending_versions(session: Session, *, limit: int = 20) -> list[DocVersion]:
    return list(
        session.execute(
            select(DocVersion)
            .where(DocVersion.parse_status.in_(["pending", "failed"]))
            .order_by(DocVersion.id)
            .limit(limit)
        ).scalars()
    )


__all__ = [
    "ingest_document",
    "rebuild_index",
    "namespace_of",
    "IngestResult",
    "IngestError",
    "DEFAULT_NAMESPACE",
    "pending_versions",
]
