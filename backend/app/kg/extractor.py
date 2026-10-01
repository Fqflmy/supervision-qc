# -*- coding: utf-8 -*-
"""规范实体与关系抽取，写入 Neo4j 与 clause_ref（SRS FR-KG-01/02/03）。"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.constants import KgNodeLabel, KgRelation
from app.core.logging_conf import get_logger
from app.db.models import ClauseRef, DocChunk, DocVersion, SpecDoc
from app.kg.graph_store import get_graph_store
from app.llm.gateway import ChatMessage, get_llm
from app.llm.prompts import KG_EXTRACT_SYSTEM, KG_EXTRACT_USER

logger = get_logger(__name__)

VALID_LABELS = {label.value for label in KgNodeLabel}
VALID_RELATIONS = {rel.value for rel in KgRelation}


@dataclass
class ExtractionResult:
    chunk_id: int
    clause_no: Optional[str]
    entities: list[dict] = field(default_factory=list)
    relations: list[dict] = field(default_factory=list)
    references: list[dict] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "clause_no": self.clause_no,
            "entities": len(self.entities),
            "relations": len(self.relations),
            "references": len(self.references),
            "error": self.error,
        }


def _normalize_entities(raw: Any) -> list[dict]:
    out: list[dict] = []
    for item in raw or []:
        if isinstance(item, str):
            item = {"text": item, "label": "Term"}
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or item.get("name") or "").strip()
        if not text or len(text) > 64:
            continue
        label = str(item.get("label") or "Term").strip()
        if label not in VALID_LABELS:
            label = "Term"
        out.append(
            {
                "text": text,
                "label": label,
                "normalized": str(item.get("normalized") or text).strip()[:64],
            }
        )
    return out[:30]


def _normalize_relations(raw: Any) -> list[dict]:
    out: list[dict] = []
    for item in raw or []:
        if not isinstance(item, dict):
            continue
        source = str(item.get("source") or "").strip()
        target = str(item.get("target") or "").strip()
        relation = str(item.get("relation") or "APPLIES_TO").upper().strip()
        if not source or not target:
            continue
        if relation not in VALID_RELATIONS:
            relation = KgRelation.APPLIES_TO.value
        out.append(
            {
                "source": source[:64],
                "target": target[:64],
                "relation": relation,
                "evidence": str(item.get("evidence") or "")[:300],
            }
        )
    return out[:40]


def _normalize_references(raw: Any) -> list[dict]:
    out: list[dict] = []
    for item in raw or []:
        if isinstance(item, str):
            item = {"clause_no": item}
        if not isinstance(item, dict):
            continue
        clause_no = str(item.get("clause_no") or item.get("clause") or "").strip()
        if not clause_no or len(clause_no) > 32:
            continue
        relation = str(item.get("relation") or "REFERENCES").upper().strip()
        if relation not in VALID_RELATIONS:
            relation = KgRelation.REFERENCES.value
        out.append(
            {
                "clause_no": clause_no,
                "spec_code": (str(item.get("spec_code") or "").strip() or None),
                "relation": relation,
                "evidence": str(item.get("evidence") or "")[:300],
            }
        )
    return out[:20]


async def extract_from_chunk(
    *,
    chunk_id: int,
    clause_no: Optional[str],
    spec_code: Optional[str],
    spec_name: Optional[str],
    content: str,
) -> ExtractionResult:
    """单条款实体关系抽取（LLM，失败返回带 error 的结果而不抛出，保证批处理继续）。"""
    prompt = KG_EXTRACT_USER.format(
        spec_code=spec_code or "未知",
        spec_name=spec_name or "未知",
        clause_no=clause_no or "未知",
        content=(content or "")[:3000],
    )
    try:
        data, _ = await get_llm().chat_json(
            [ChatMessage("system", KG_EXTRACT_SYSTEM), ChatMessage("user", prompt)],
            scene="kg_extract",
            temperature=0.0,
            max_tokens=2048,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("图谱抽取失败", extra={"chunk_id": chunk_id, "error": str(exc)[:200]})
        return ExtractionResult(chunk_id=chunk_id, clause_no=clause_no, error=str(exc)[:300])

    payload = data if isinstance(data, dict) else {}
    return ExtractionResult(
        chunk_id=chunk_id,
        clause_no=clause_no,
        entities=_normalize_entities(payload.get("entities")),
        relations=_normalize_relations(payload.get("relations")),
        references=_normalize_references(payload.get("references")),
    )


def persist_extraction(
    session: Session,
    *,
    result: ExtractionResult,
    spec_code: Optional[str],
    spec_name: Optional[str],
    content: str,
    use_graph: bool = True,
) -> dict[str, int]:
    """把抽取结果写入 Neo4j（图谱）与 PostgreSQL（clause_ref 冗余索引）。"""
    stats = {"entities": 0, "relations": 0, "references": 0}
    store = get_graph_store() if use_graph else None

    if store is not None and store.available:
        if spec_code:
            store.upsert_spec(spec_code, spec_name or spec_code)
        store.upsert_clause(
            clause_id=result.chunk_id,
            clause_no=result.clause_no,
            spec_code=spec_code,
            content=content,
        )
    for entity in result.entities:
        stats["entities"] += 1
        if store is not None and store.available:
            store.upsert_entity(entity["text"], entity["label"])
            if result.clause_no:
                store.link_entity_to_clause(entity["text"], result.clause_no, "DEFINES")
    for relation in result.relations:
        stats["relations"] += 1
        if store is not None and store.available:
            store.upsert_entity(relation["source"], "Term")
            store.upsert_entity(relation["target"], "Term")
    for ref in result.references:
        stats["references"] += 1
        if store is not None and store.available:
            store.link_clauses(
                result.clause_no,
                ref["clause_no"],
                ref["relation"],
                src_spec_code=spec_code,
                dst_spec_code=ref.get("spec_code"),
                confidence=0.8,
                evidence=ref.get("evidence"),
            )
        session.add(
            ClauseRef(
                src_clause_id=result.chunk_id,
                src_clause_no=result.clause_no,
                dst_clause_no=ref["clause_no"],
                dst_spec_code=ref.get("spec_code"),
                relation=ref["relation"],
                confidence=0.8,
                source="llm",
                evidence=ref.get("evidence"),
            )
        )
    return stats


def _load_chunks(
    session: Session, doc_id: int, *, limit: Optional[int] = None, only_clauses: bool = True
):
    stmt = (
        select(DocChunk, DocVersion, SpecDoc)
        .join(DocVersion, DocChunk.doc_version_id == DocVersion.id)
        .join(SpecDoc, DocChunk.doc_id == SpecDoc.id)
        .where(DocChunk.doc_id == doc_id, DocChunk.status == "active")
        .order_by(DocChunk.chunk_index)
    )
    if only_clauses:
        stmt = stmt.where(DocChunk.clause_no.isnot(None))
    if limit:
        stmt = stmt.limit(limit)
    return session.execute(stmt).all()


async def build_graph_for_doc(
    session: Session,
    doc_id: int,
    *,
    limit: Optional[int] = None,
    concurrency: int = 4,
    use_graph: bool = True,
) -> dict[str, Any]:
    """为一份规范文档构建图谱（FR-KG-03）。"""
    rows = _load_chunks(session, doc_id, limit=limit)
    if not rows:
        return {"doc_id": doc_id, "chunks": 0, "message": "没有可抽取的条款块"}

    semaphore = asyncio.Semaphore(max(1, concurrency))
    totals = {"entities": 0, "relations": 0, "references": 0, "failed": 0}

    async def one(chunk: DocChunk, doc: SpecDoc) -> Optional[ExtractionResult]:
        async with semaphore:
            return await extract_from_chunk(
                chunk_id=int(chunk.id),
                clause_no=chunk.clause_no,
                spec_code=doc.spec_code,
                spec_name=doc.spec_name,
                content=chunk.content,
            )

    results = await asyncio.gather(*[one(chunk, doc) for chunk, _version, doc in rows])
    for result, (chunk, _version, doc) in zip(results, rows):
        if result is None:
            continue
        if result.error:
            totals["failed"] += 1
            continue
        stats = persist_extraction(
            session,
            result=result,
            spec_code=doc.spec_code,
            spec_name=doc.spec_name,
            content=chunk.content,
            use_graph=use_graph,
        )
        for key, value in stats.items():
            totals[key] += value

    doc_row = session.get(SpecDoc, doc_id)
    if doc_row is not None:
        doc_row.kg_built = True
    logger.info("图谱构建完成", extra={"doc_id": doc_id, "chunks": len(rows), **totals})
    return {"doc_id": doc_id, "chunks": len(rows), **totals}


def link_intra_doc_clauses(session: Session, doc_id: int) -> int:
    """把 clause_ref 中能解析到本文档条款的记录补全 dst_clause_id（FR-KG-04）。"""
    from sqlalchemy import update

    chunks = session.execute(
        select(DocChunk.clause_no, DocChunk.id).where(
            DocChunk.doc_id == doc_id, DocChunk.clause_no.isnot(None)
        )
    ).all()
    clause_map = {clause_no: int(cid) for clause_no, cid in chunks if clause_no}
    if not clause_map:
        return 0
    refs = session.execute(
        select(ClauseRef).where(ClauseRef.src_clause_id.in_(list(clause_map.values())))
    ).scalars().all()
    updated = 0
    for ref in refs:
        target_id = clause_map.get(ref.dst_clause_no)
        if target_id and ref.dst_clause_id != target_id:
            ref.dst_clause_id = target_id
            updated += 1
    session.flush()
    return updated


__all__ = [
    "ExtractionResult",
    "extract_from_chunk",
    "persist_extraction",
    "build_graph_for_doc",
    "link_intra_doc_clauses",
]
