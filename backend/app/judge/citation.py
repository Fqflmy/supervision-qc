# -*- coding: utf-8 -*-
"""引用校验：识别幻觉引用（SRS FR-JDG-03）。

三道校验：
1. 存在性——条款是否真实存在于知识库（PostgreSQL doc_chunk + Neo4j 双重确认）；
2. 版本有效性——引用的规范是否已废止；
3. 语义一致性——LLM 判断结论能否由该条款支撑。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import DocStatus
from app.core.logging_conf import get_logger
from app.config import settings
from app.db.models import DocChunk, SpecDoc
from app.kg.graph_store import get_graph_store
from app.llm.gateway import ChatMessage, get_llm
from app.llm.prompts import CITATION_VERIFY_SYSTEM, CITATION_VERIFY_USER

logger = get_logger(__name__)


@dataclass
class CitationCheckItem:
    clause_no: Optional[str]
    spec_code: Optional[str]
    exists: bool = False
    source: str = "none"  # db | graph | db+graph | none
    spec_status: Optional[str] = None
    abolished: bool = False
    semantic_consistent: Optional[bool] = None
    semantic_reason: Optional[str] = None
    content_preview: Optional[str] = None

    @property
    def is_hallucination(self) -> bool:
        return not self.exists

    def to_dict(self) -> dict[str, Any]:
        return {
            "clause_no": self.clause_no,
            "spec_code": self.spec_code,
            "exists": self.exists,
            "source": self.source,
            "spec_status": self.spec_status,
            "abolished": self.abolished,
            "hallucination": self.is_hallucination,
            "semantic_consistent": self.semantic_consistent,
            "semantic_reason": self.semantic_reason,
            "content_preview": self.content_preview,
        }


@dataclass
class CitationCheckResult:
    total: int = 0
    valid: int = 0
    hallucinations: int = 0
    abolished: int = 0
    inconsistent: int = 0
    items: list[CitationCheckItem] = field(default_factory=list)
    available: bool = True

    @property
    def accuracy(self) -> float:
        if not self.total:
            return 1.0
        return round(self.valid / self.total, 4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "valid": self.valid,
            "hallucinations": self.hallucinations,
            "abolished": self.abolished,
            "inconsistent": self.inconsistent,
            "accuracy": self.accuracy,
            "available": self.available,
            "items": [i.to_dict() for i in self.items],
        }


def _lookup_in_db(session: Session, clause_nos: Sequence[str]) -> dict[str, tuple[DocChunk, Optional[SpecDoc]]]:
    if not clause_nos:
        return {}
    rows = session.execute(
        select(DocChunk, SpecDoc)
        .outerjoin(SpecDoc, DocChunk.doc_id == SpecDoc.id)
        .where(DocChunk.clause_no.in_(list(clause_nos)))
        .limit(200)
    ).all()
    out: dict[str, tuple[DocChunk, Optional[SpecDoc]]] = {}
    for chunk, doc in rows:
        if chunk.clause_no and chunk.clause_no not in out:
            out[chunk.clause_no] = (chunk, doc)
    return out


@dataclass
class CitationPenalty:
    """引用校验折算到「条款引用准确性」维度的扣分。"""

    points: float = 0.0
    reasons: list[str] = field(default_factory=list)

    def apply(self, score: float) -> tuple[float, Optional[str]]:
        if self.points <= 0:
            return score, None
        new_score = max(0.0, round(score - self.points, 2))
        return new_score, "[引用校验] " + "；".join(self.reasons)

    def to_dict(self) -> dict[str, Any]:
        return {"points": round(self.points, 2), "reasons": list(self.reasons)}


def citation_penalty(
    result: CitationCheckResult,
    *,
    per_hallucination: Optional[float] = None,
    per_abolished: Optional[float] = None,
    per_inconsistent: Optional[float] = None,
    max_penalty: Optional[float] = None,
) -> CitationPenalty:
    """把引用校验结果换算成扣分。

    采用**按比例**扣分而非固定重罚：1 条错误引用不应让准确性维度直接归零，
    否则会掩盖真实得分、误导人工复核。参数均可通过配置调整。
    """
    if not result.total or result.available is False:
        return CitationPenalty()

    per_hallucination = (
        settings.judge_penalty_hallucination if per_hallucination is None else per_hallucination
    )
    per_abolished = settings.judge_penalty_abolished if per_abolished is None else per_abolished
    per_inconsistent = (
        settings.judge_penalty_inconsistent if per_inconsistent is None else per_inconsistent
    )
    max_penalty = settings.judge_penalty_max if max_penalty is None else max_penalty

    raw = (
        result.hallucinations * per_hallucination
        + result.abolished * per_abolished
        + result.inconsistent * per_inconsistent
    )
    if raw <= 0:
        return CitationPenalty()

    reasons: list[str] = []
    if result.hallucinations:
        reasons.append(f"发现 {result.hallucinations} 条引用在知识库中不存在")
    if result.abolished:
        reasons.append(f"{result.abolished} 条引用指向已废止规范")
    if result.inconsistent:
        reasons.append(f"{result.inconsistent} 条引用与结论语义不一致")
    reasons.append(f"引用准确率 {(result.accuracy * 100):.1f}%")

    return CitationPenalty(points=min(max_penalty, round(raw, 2)), reasons=reasons)


def check_citations(
    session: Optional[Session],
    citations: Sequence[dict[str, Any]],
    *,
    use_graph: bool = True,
    semantic_check: bool = False,
) -> CitationCheckResult:
    """批量校验引用（同步部分：存在性与版本）。语义一致性由 async 版本补齐。"""
    result = CitationCheckResult()
    if not citations:
        return result

    clause_nos = [str(c.get("clause_no")) for c in citations if c.get("clause_no")]
    db_map = _lookup_in_db(session, clause_nos) if session is not None else {}
    graph_nos: set[str] = set()
    store = get_graph_store() if use_graph else None
    if store is not None and store.available and clause_nos:
        graph_nos = store.existing_clause_nos(clause_nos)

    for citation in citations:
        clause_no = citation.get("clause_no")
        spec_code = citation.get("spec_code")
        item = CitationCheckItem(clause_no=clause_no, spec_code=spec_code)
        in_db = clause_no in db_map if clause_no else False
        in_graph = clause_no in graph_nos if clause_no else False
        if in_db and in_graph:
            item.exists, item.source = True, "db+graph"
        elif in_db:
            item.exists, item.source = True, "db"
        elif in_graph:
            item.exists, item.source = True, "graph"
        else:
            item.exists, item.source = False, "none"

        if in_db:
            chunk, doc = db_map[clause_no]
            item.content_preview = (chunk.content or "")[:300]
            if doc is not None:
                item.spec_status = doc.status
                item.abolished = doc.status == DocStatus.ABOLISHED.value
                item.spec_code = item.spec_code or doc.spec_code

        result.total += 1
        if item.exists:
            result.valid += 1
        else:
            result.hallucinations += 1
        if item.abolished:
            result.abolished += 1
        result.items.append(item)
    return result


async def check_citations_async(
    session: Optional[Session],
    citations: Sequence[dict[str, Any]],
    *,
    conclusions: Optional[Sequence[str]] = None,
    use_graph: bool = True,
    semantic_check: bool = False,
    semantic_limit: int = 5,
) -> CitationCheckResult:
    import asyncio as _asyncio

    result = await _asyncio.to_thread(
        check_citations, session, citations, use_graph=use_graph, semantic_check=False
    )
    if not semantic_check:
        return result

    candidates = [
        (idx, item, conclusions[idx] if conclusions and idx < len(conclusions) else "")
        for idx, item in enumerate(result.items)
        if item.exists and item.content_preview
    ][:semantic_limit]
    if not candidates:
        return result

    async def verify(_idx: int, item: CitationCheckItem, conclusion: str) -> tuple[CitationCheckItem, bool, str]:
        if not conclusion:
            return item, True, "无对应结论，跳过语义校验"
        try:
            data, _ = await get_llm().chat_json(
                [
                    ChatMessage("system", CITATION_VERIFY_SYSTEM),
                    ChatMessage(
                        "user",
                        CITATION_VERIFY_USER.format(
                            clause_content=item.content_preview, conclusion=conclusion[:500]
                        ),
                    ),
                ],
                scene="citation_verify",
                temperature=0.0,
                max_tokens=512,
            )
            payload = data if isinstance(data, dict) else {}
            return item, bool(payload.get("consistent", True)), str(payload.get("reason") or "")[:300]
        except Exception as exc:  # noqa: BLE001
            logger.warning("语义一致性校验失败", extra={"clause_no": item.clause_no, "error": str(exc)[:160]})
            return item, True, f"校验不可用：{str(exc)[:120]}"

    outcomes = await asyncio.gather(*[verify(i, item, text) for i, item, text in candidates])
    for item, consistent, reason in outcomes:
        item.semantic_consistent = consistent
        item.semantic_reason = reason
        if not consistent:
            result.inconsistent += 1
    return result


__all__ = [
    "CitationCheckItem",
    "CitationCheckResult",
    "CitationPenalty",
    "citation_penalty",
    "check_citations",
    "check_citations_async",
]
