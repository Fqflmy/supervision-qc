# -*- coding: utf-8 -*-
"""文本块仓储：从 PostgreSQL 读取检索所需的块与规范元数据（SRS 5.2）。"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import DocChunk, DocVersion, SpecDoc
from app.retrieval.types import ChunkRecord


def fetch_chunk_records(
    session: Session,
    chunk_ids: Sequence[int],
    *,
    only_published: bool = True,
    kb_ids: Optional[Sequence[int]] = None,
) -> dict[int, ChunkRecord]:
    """批量按 chunk_id 取块内容与规范元数据（保持输入顺序无关，返回字典）。"""
    ids = [int(cid) for cid in chunk_ids]
    if not ids:
        return {}
    stmt = (
        select(DocChunk, DocVersion, SpecDoc)
        .join(DocVersion, DocChunk.doc_version_id == DocVersion.id)
        .join(SpecDoc, DocChunk.doc_id == SpecDoc.id)
        .where(DocChunk.id.in_(ids))
    )
    if only_published:
        stmt = stmt.where(SpecDoc.status == "published")
    if kb_ids:
        stmt = stmt.where(SpecDoc.kb_id.in_([int(k) for k in kb_ids]))

    records: dict[int, ChunkRecord] = {}
    for chunk, version, doc in session.execute(stmt).all():
        records[int(chunk.id)] = ChunkRecord(
            chunk_id=int(chunk.id),
            content=chunk.content or "",
            doc_id=int(chunk.doc_id),
            version_id=int(chunk.doc_version_id),
            clause_no=chunk.clause_no,
            chapter_path=chunk.chapter_path,
            page_no=chunk.page_no,
            spec_code=doc.spec_code,
            spec_name=doc.spec_name,
            token_count=chunk.token_count,
        )
    return records


def fetch_chunks_by_clause(
    session: Session, clause_nos: Iterable[str], *, only_published: bool = True, limit: int = 20
) -> dict[str, ChunkRecord]:
    """按条款号精确取块，用于图谱引用链扩展（SRS FR-KG-05）。"""
    nos = [n for n in clause_nos if n]
    if not nos:
        return {}
    stmt = (
        select(DocChunk, DocVersion, SpecDoc)
        .join(DocVersion, DocChunk.doc_version_id == DocVersion.id)
        .join(SpecDoc, DocChunk.doc_id == SpecDoc.id)
        .where(DocChunk.clause_no.in_(nos))
        .limit(limit)
    )
    if only_published:
        stmt = stmt.where(SpecDoc.status == "published")
    out: dict[str, ChunkRecord] = {}
    for chunk, version, doc in session.execute(stmt).all():
        out.setdefault(
            chunk.clause_no,
            ChunkRecord(
                chunk_id=int(chunk.id),
                content=chunk.content or "",
                doc_id=int(chunk.doc_id),
                version_id=int(chunk.doc_version_id),
                clause_no=chunk.clause_no,
                chapter_path=chunk.chapter_path,
                page_no=chunk.page_no,
                spec_code=doc.spec_code,
                spec_name=doc.spec_name,
                token_count=chunk.token_count,
            ),
        )
    return out


def count_active_chunks(session: Session, *, only_published: bool = True) -> int:
    from sqlalchemy import func

    stmt = select(func.count(DocChunk.id)).where(DocChunk.status == "active")
    if only_published:
        stmt = stmt.join(SpecDoc, DocChunk.doc_id == SpecDoc.id).where(SpecDoc.status == "published")
    return int(session.execute(stmt).scalar() or 0)


__all__ = ["fetch_chunk_records", "fetch_chunks_by_clause", "count_active_chunks"]
