# -*- coding: utf-8 -*-
"""多阶段检索 Pipeline（SRS FR-RET-01）。

流程：查询理解 → Multi-Query 改写 → BM25/向量混合召回 → RRF 融合
      → 图谱增强扩展 → Reranker 精排 → 无依据判定 → 上下文组装

所有阶段都记录到 ``debug``，失败按 FR-AGT-10 降级而不是整体报错。
"""
from __future__ import annotations

import asyncio
import time
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.config import settings
from app.core.logging_conf import get_logger
from app.llm.tokens import count_tokens
from app.retrieval.bm25 import get_bm25_index
from app.retrieval.embedding import get_embedder
from app.retrieval.fusion import FusedItem, RankedItem, rrf_fuse
from app.retrieval.query import generate_sub_queries, understand_query
from app.retrieval.repository import fetch_chunk_records
from app.retrieval.types import ChunkRecord, RetrievalResult, RetrievedClause
from app.retrieval.vector_store import get_faiss_index

logger = get_logger(__name__)


class RetrievalPipeline:
    """检索管道。

    namespace 为 None 时跨全部分片（多知识库）召回，合并后统一融合排序；
    指定 namespace 则只检索该知识库分片。
    """

    def __init__(self, namespace: Optional[str] = None) -> None:
        self.namespace = namespace

    # ------------------------------------------------------------------ #
    # 分片解析
    # ------------------------------------------------------------------ #
    def _namespaces(self) -> list[str]:
        if self.namespace:
            return [self.namespace]
        from app.retrieval.vector_store import discover_namespaces

        found = discover_namespaces()
        return found or ["default"]

    # ------------------------------------------------------------------ #
    # 各阶段
    # ------------------------------------------------------------------ #
    def _bm25_channel(self, query: str, namespace: str) -> list[RankedItem]:
        hits = get_bm25_index(namespace).search(query, top_k=settings.bm25_top_k)
        return [RankedItem(h.chunk_id, h.score, h.rank, "bm25") for h in hits]

    def _dense_channel(
        self, query: str, namespace: str, vector: Optional[list[float]] = None
    ) -> list[RankedItem]:
        if vector is None:
            vector = get_embedder().encode_one(query, is_query=True)
        hits = get_faiss_index(namespace).search(vector, top_k=settings.dense_top_k)
        return [RankedItem(h.chunk_id, h.score, h.rank, "dense") for h in hits]

    def _bm25_many(self, queries: Sequence[str]) -> list[list[RankedItem]]:
        out: list[list[RankedItem]] = []
        for namespace in self._namespaces():
            for query in queries:
                out.append(self._bm25_channel(query, namespace))
        return out

    def _dense_many(self, queries: Sequence[str]) -> list[list[RankedItem]]:
        """查询向量只算一次，各分片复用（避免重复推理）。"""
        embedder = get_embedder()
        out: list[list[RankedItem]] = []
        for query in queries:
            vector = embedder.encode_one(query, is_query=True)
            for namespace in self._namespaces():
                out.append(self._dense_channel(query, namespace, vector=vector))
        return out

    async def _hybrid_recall(
        self, sub_queries: Sequence[str]
    ) -> tuple[list[FusedItem], dict[str, Any]]:
        """并行执行 BM25 与向量召回（SRS FR-RET-04）。"""
        loop = asyncio.get_running_loop()
        bm25_task = loop.run_in_executor(None, self._bm25_many, list(sub_queries))
        dense_task = loop.run_in_executor(None, self._dense_many, list(sub_queries))
        results = await asyncio.gather(bm25_task, dense_task, return_exceptions=True)

        bm25_lists: list[list[RankedItem]] = []
        dense_lists: list[list[RankedItem]] = []
        errors: list[str] = []
        if isinstance(results[0], Exception):
            errors.append(f"bm25: {results[0]}")
            logger.warning("BM25 召回失败，降级为向量单通道", extra={"error": str(results[0])[:200]})
        else:
            bm25_lists = results[0]  # type: ignore[assignment]
        if isinstance(results[1], Exception):
            errors.append(f"dense: {results[1]}")
            logger.warning("向量召回失败，降级为 BM25 单通道", extra={"error": str(results[1])[:200]})
        else:
            dense_lists = results[1]  # type: ignore[assignment]

        if not bm25_lists and not dense_lists:
            return [], {"errors": errors, "channels": {"bm25": 0, "dense": 0}}

        # 每一路（子查询/通道）等权参与 RRF；通道权重由 bm25_weight / dense_weight 表达
        lists: list[Sequence[RankedItem]] = []
        weights: list[float] = []
        for items in bm25_lists:
            lists.append(items)
            weights.append(settings.bm25_weight)
        for items in dense_lists:
            lists.append(items)
            weights.append(settings.dense_weight)

        fused = rrf_fuse(lists, weights=weights, k=settings.rrf_k, top_k=settings.fusion_top_k)
        debug = {
            "errors": errors,
            "namespaces": self._namespaces(),
            "channels": {
                "bm25": sum(len(x) for x in bm25_lists),
                "dense": sum(len(x) for x in dense_lists),
            },
            "fused": len(fused),
        }
        return fused, debug

    def _kg_expand(self, fused: list[FusedItem], session: Optional[Session]) -> dict[int, list[dict]]:
        """图谱增强：命中条款的上下位/替代条款补充召回（SRS FR-KG-05）。"""
        if not settings.kg_expand_enabled or session is None or not fused:
            return {}
        try:
            from app.kg.graph_store import get_graph_store
            from app.retrieval.repository import fetch_chunk_records as _fetch

            store = get_graph_store()
            if not store.available:
                return {}
            seeds = _fetch(session, [f.chunk_id for f in fused[: settings.fusion_top_k]])
            clause_nos = [r.clause_no for r in seeds.values() if r.clause_no]
            related: dict[int, list[dict]] = {}
            extra_clause_nos: set[str] = set()
            for clause_no in clause_nos[:10]:
                neighbors = store.neighbors(clause_no, depth=1, limit=settings.kg_expand_limit)
                if neighbors:
                    related[clause_no] = neighbors
                    extra_clause_nos.update(
                        n.get("clause_no") for n in neighbors if n.get("clause_no")
                    )
            if extra_clause_nos:
                extra = _fetch_by_clause(session, extra_clause_nos)
                for chunk in extra.values():
                    if all(chunk.chunk_id != f.chunk_id for f in fused):
                        fused.append(FusedItem(chunk_id=chunk.chunk_id, rrf_score=0.0, sources=["kg_expand"]))
            return related
        except Exception as exc:  # noqa: BLE001
            logger.warning("图谱增强失败，跳过", extra={"error": str(exc)[:200]})
            return {}

    def _rerank(self, query: str, fused: list[FusedItem], records: dict[int, ChunkRecord]) -> list[tuple[int, float]]:
        """Reranker 精排（FR-RET-06），失败回退 RRF 顺序。"""
        candidates = [f for f in fused if f.chunk_id in records]
        if not candidates:
            return []
        if not settings.rerank_enabled:
            return [(f.chunk_id, f.rrf_score) for f in candidates[: settings.reranker_top_n]]
        try:
            from app.retrieval.reranker import get_reranker

            documents = [records[f.chunk_id].content for f in candidates]
            ranked = get_reranker().rerank(query, documents, settings.reranker_top_n)
            return [(candidates[r.index].chunk_id, r.score) for r in ranked]
        except Exception as exc:  # noqa: BLE001
            logger.warning("Rerank 失败，回退 RRF 排序", extra={"error": str(exc)[:200]})
            return [(f.chunk_id, f.rrf_score) for f in candidates[: settings.reranker_top_n]]

    # ------------------------------------------------------------------ #
    # 主入口
    # ------------------------------------------------------------------ #
    async def retrieve(
        self,
        query: str,
        session: Session,
        *,
        kb_ids: Optional[Sequence[int]] = None,
        specialty: Optional[str] = None,
        top_k: Optional[int] = None,
        enable_multi_query: Optional[bool] = None,
        enable_kg_expand: Optional[bool] = None,
        fused_input: Optional[list[FusedItem]] = None,
    ) -> RetrievalResult:
        started = time.perf_counter()
        top_n = top_k or settings.reranker_top_n

        # 1) 查询理解
        understanding = understand_query(query, specialty=specialty, kb_ids=kb_ids)

        # 2) Multi-Query 改写
        if enable_multi_query is False:
            sub_queries = [query]
        else:
            sub_queries = await generate_sub_queries(query, understanding)

        # 3) 混合召回 + RRF
        if fused_input is None:
            fused, recall_debug = await self._hybrid_recall(sub_queries)
        else:
            fused, recall_debug = fused_input, {"channels": {"injected": len(fused_input)}}

        # 4) 图谱增强
        if enable_kg_expand is False or not settings.kg_expand_enabled:
            kg_related: dict[str, list[dict]] = {}
        else:
            kg_related = self._kg_expand(fused, session)

        # 5) 取回块内容（过滤未发布/越权知识库）
        records = fetch_chunk_records(
            session, [f.chunk_id for f in fused], only_published=True, kb_ids=kb_ids
        )

        # 6) 精排
        ranked = self._rerank(query, fused, records)[:top_n]

        # 7) 组装结果 + 无依据判定（FR-RET-10）
        clauses: list[RetrievedClause] = []
        for chunk_id, relevance in ranked:
            record = records.get(chunk_id)
            if record is None:
                continue
            entry = next((f for f in fused if f.chunk_id == chunk_id), None)
            citations = []
            if record.clause_no and record.clause_no in kg_related:
                citations = kg_related[record.clause_no]
            clauses.append(
                RetrievedClause(
                    chunk_id=chunk_id,
                    content=record.content,
                    citation=record.citation(),
                    rrf_score=entry.rrf_score if entry else 0.0,
                    relevance_score=relevance,
                    sources=list(entry.sources) if entry else [],
                    best_rank=entry.best_rank if entry else 10**6,
                    kg_related=citations,
                    degraded=get_embedder().degraded,
                )
            )

        top_score = clauses[0].relevance_score if clauses else 0.0
        no_evidence = not clauses or top_score < settings.no_evidence_threshold
        latency_ms = int((time.perf_counter() - started) * 1000)

        logger.info(
            "检索完成",
            extra={
                "query": query[:80],
                "sub_queries": len(sub_queries),
                "fused": len(fused),
                "returned": len(clauses),
                "top_score": round(top_score, 4),
                "no_evidence": no_evidence,
                "latency_ms": latency_ms,
            },
        )
        return RetrievalResult(
            query=query,
            understanding=understanding,
            sub_queries=sub_queries,
            clauses=clauses,
            no_evidence=no_evidence,
            latency_ms=latency_ms,
            degraded=get_embedder().degraded,
            debug={
                "recall": recall_debug,
                "kg_expanded": sum(len(v) for v in kg_related.values()),
                "rerank_top_score": round(top_score, 4),
                "threshold": settings.no_evidence_threshold,
                "embedder": get_embedder().name,
                "reranker": get_reranker_name(),
            },
        )


def get_reranker_name() -> str:
    if not settings.rerank_enabled:
        return "disabled"
    try:
        from app.retrieval.reranker import get_reranker

        return get_reranker().name
    except Exception:  # noqa: BLE001
        return "unavailable"


def _fetch_by_clause(session: Session, clause_nos: Sequence[str]) -> dict[str, ChunkRecord]:
    from app.retrieval.repository import fetch_chunks_by_clause

    return fetch_chunks_by_clause(session, clause_nos, limit=50)


def assemble_context(
    clauses: Sequence[RetrievedClause], *, token_budget: Optional[int] = None
) -> tuple[str, list[RetrievedClause]]:
    """上下文组装（FR-RET-07）：按相关性排序、去重、控制 token 预算、保留条款完整性。"""
    budget = token_budget or settings.context_token_budget
    used = 0
    parts: list[str] = []
    kept: list[RetrievedClause] = []
    seen: set[str] = set()
    for clause in clauses:
        fingerprint = (clause.citation.clause_no or "") + clause.content[:80]
        if fingerprint in seen:
            continue
        block = f"【依据 {len(kept) + 1}】{clause.citation.render()}\n{clause.content.strip()}"
        cost = count_tokens(block)
        if used + cost > budget and kept:
            break
        seen.add(fingerprint)
        parts.append(block)
        kept.append(clause)
        used += cost
    return "\n\n".join(parts), kept


__all__ = ["RetrievalPipeline", "assemble_context", "get_reranker_name"]
