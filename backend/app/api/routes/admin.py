# -*- coding: utf-8 -*-
"""系统接口：健康检查、审计日志、模型配置、统计（FR-SYS，SRS 6.2 API-19/20）。"""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Body, Query
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, require_permission
from app.config import settings
from app.constants import AuditAction, ERROR_CODES, EvalState
from app.db.models import AuditLog, DocChunk, EvalTask, SpecDoc, User
from app.core.response import ok, paginate
from app.retrieval.embedding import get_embedder
from app.retrieval.reranker import get_reranker
from app.retrieval.vector_store import discover_namespaces, index_stats

router = APIRouter(tags=["系统"])


def vector_index_summary() -> dict:
    """汇总向量索引状态。

    索引按知识库分片存放（命名空间形如 kb_1 / kb_2），**不存在名为 default 的
    分片**。早期版本硬编码查 "default"，导致健康检查永远上报 size=0，
    看起来「索引是空的」但其实检索完全正常，容易误导排查方向。
    """
    namespaces = discover_namespaces()
    if not namespaces:
        return {"name": "-", "namespaces": [], "size": 0, "dim": get_embedder().dim, "dirty": False}
    total = 0
    dim = 0
    dirty = False
    for name in namespaces:
        stats = index_stats(name)
        total += int(stats.get("size") or 0)
        dim = dim or int(stats.get("dim") or 0)
        dirty = dirty or bool(stats.get("dirty"))
    return {
        "name": ",".join(namespaces),
        "namespaces": namespaces,
        "size": total,
        "dim": dim or get_embedder().dim,
        "dirty": dirty,
    }


@router.get("/health", summary="健康检查（API-20）")
def health() -> dict:
    from app.db.session import ping
    from app.kg.graph_store import get_graph_store

    store = get_graph_store()
    components = {
        "database": {"ok": ping(), "url_kind": "sqlite" if settings.is_sqlite else "postgresql"},
        "neo4j": {"enabled": settings.neo4j_enabled, "ok": store.available},
        "embedding": {"name": get_embedder().name, "dim": get_embedder().dim, "degraded": get_embedder().degraded},
        "reranker": {"name": get_reranker().name, "degraded": get_reranker().degraded},
        "llm": {
            "provider": settings.llm_provider,
            "primary_model": settings.llm_primary_model,
            "fallback_configured": bool(settings.llm_fallback_api_key),
            "primary_key_configured": bool(settings.llm_primary_api_key),
        },
        "vector_index_default": vector_index_summary(),
    }
    overall = "ok" if components["database"]["ok"] else "degraded"
    return ok(
        {
            "status": overall,
            "app": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "components": components,
        }
    )


@router.get("/metrics", summary="Prometheus 指标")
def metrics(session: DbSession, user: CurrentUser) -> dict:
    """以 JSON 形式暴露关键指标；生产建议接入 prometheus_client。"""
    doc_count = int(session.execute(select(func.count(SpecDoc.id))).scalar() or 0)
    chunk_count = int(session.execute(select(func.count(DocChunk.id))).scalar() or 0)
    task_rows = session.execute(
        select(EvalTask.current_state, func.count(EvalTask.id)).group_by(EvalTask.current_state)
    ).all()
    token_sum = session.execute(select(func.coalesce(func.sum(EvalTask.total_tokens), 0))).scalar()
    return ok(
        {
            "documents_total": doc_count,
            "chunks_total": chunk_count,
            "tasks_by_state": {str(s): int(c) for s, c in task_rows},
            "tokens_total": int(token_sum or 0),
            "vector_index": vector_index_summary(),
            "error_codes": ERROR_CODES,
        }
    )


@router.get("/admin/audit-logs", summary="审计日志查询（API-19）")
def audit_logs(
    session: DbSession,
    user: Annotated[User, require_permission("admin:*")],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    action: Optional[str] = None,
    username: Optional[str] = None,
) -> dict:
    stmt = select(AuditLog)
    if action:
        stmt = stmt.where(AuditLog.action == action)
    if username:
        stmt = stmt.where(AuditLog.username == username)
    stmt = stmt.order_by(AuditLog.id.desc())

    page = max(1, page)
    page_size = min(100, max(1, page_size))
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int(session.execute(count_stmt).scalar() or 0)
    rows = session.execute(stmt.limit(page_size).offset((page - 1) * page_size)).scalars().all()
    items = [
        {
            "id": r.id,
            "trace_id": r.trace_id,
            "user_id": r.user_id,
            "username": r.username,
            "action": r.action,
            "object_type": r.object_type,
            "object_id": r.object_id,
            "result": r.result,
            "ip": r.ip,
            "detail": r.detail,
            "created_at": r.created_at,
        }
        for r in rows
    ]
    return ok(paginate(items, total, page, page_size))


@router.get("/admin/config", summary="查看运行配置（脱敏）")
def get_config(session: DbSession, user: Annotated[User, require_permission("admin:*")]) -> dict:
    return ok(
        {
            "retrieval": {
                "chunk_size": settings.chunk_size,
                "chunk_overlap": settings.chunk_overlap,
                "bm25_top_k": settings.bm25_top_k,
                "dense_top_k": settings.dense_top_k,
                "multi_query_n": settings.multi_query_n,
                "rrf_k": settings.rrf_k,
                "bm25_weight": settings.bm25_weight,
                "dense_weight": settings.dense_weight,
                "rerank_top_n": settings.reranker_top_n,
                "no_evidence_threshold": settings.no_evidence_threshold,
            },
            "agent": {
                "max_iterations": settings.agent_max_iterations,
                "no_progress_limit": settings.agent_no_progress_limit,
                "tool_timeout_seconds": settings.agent_tool_timeout_seconds,
                "task_timeout_seconds": settings.agent_task_timeout_seconds,
                "token_budget": settings.agent_token_budget,
                "checkpoint_backend": settings.checkpoint_backend,
            },
            "judge": {
                "threshold": settings.judge_threshold,
                "conflict_delta": settings.judge_conflict_delta,
                "dimensions": settings.judge_dimension_list,
            },
            "llm": {
                "provider": settings.llm_provider,
                "primary_model": settings.llm_primary_model,
                "primary_base_url": settings.llm_primary_base_url,
                "primary_api_key": "***" if settings.llm_primary_api_key else "",
                "fallback_model": settings.llm_fallback_model,
                "fallback_api_key": "***" if settings.llm_fallback_api_key else "",
                "temperature": settings.llm_temperature,
            },
        }
    )


@router.post("/admin/config", summary="更新运行配置（写入 system_config，重启后仍以环境变量为准）")
def update_config(
    session: DbSession,
    user: Annotated[User, require_permission("admin:*")],
    body: dict = Body(...),
) -> dict:
    from app.db.models import SystemConfig

    saved = []
    for key, value in (body or {}).items():
        row = session.execute(
            select(SystemConfig).where(SystemConfig.key == key)
        ).scalars().first()
        if row is None:
            row = SystemConfig(key=key, value={"value": value}, category="runtime")
            session.add(row)
        else:
            row.value = {"value": value}
        saved.append(key)
    from app.db import add_audit_log

    add_audit_log(session, AuditAction.CONFIG_CHANGE.value, detail={"keys": saved})
    session.commit()
    return ok({"updated": saved})


@router.post("/admin/rebuild-index", summary="全量重建检索索引")
async def rebuild(
    session: DbSession,
    user: Annotated[User, require_permission("admin:*")],
) -> dict:
    from app.services.ingest import rebuild_index

    stats = await rebuild_index(session)
    from app.db import add_audit_log

    add_audit_log(session, AuditAction.CONFIG_CHANGE.value, detail={"rebuild_index": stats})
    session.commit()
    return ok(stats)
