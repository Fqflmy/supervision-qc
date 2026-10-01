# -*- coding: utf-8 -*-
"""检索与智能问答接口（FR-RET，SRS 6.2 API-10/11）。"""
from __future__ import annotations

import time

from typing import Optional

from fastapi import APIRouter, Body, Query, Request

from app.api.deps import CurrentUser, DbSession, client_ip
from app.constants import AuditAction
from app.core.logging_conf import get_logger
from app.core.response import ok
from app.db import add_audit_log
from app.llm.gateway import ChatMessage, get_llm
from app.llm.json_utils import extract_json
from app.llm.prompts import RETRIEVAL_ANSWER_SYSTEM, RETRIEVAL_ANSWER_USER
from app.retrieval.pipeline import RetrievalPipeline, assemble_context
from app.schemas.api import ChatRequest, RetrievalRequest

router = APIRouter(prefix="/retrieval", tags=["检索"])
logger = get_logger(__name__)


def _extract_answer(content: str) -> str:
    """把模型输出转成给用户看的纯文本。

    部分模型（含本项目约定格式）会返回 {"answer": "...", "used_clause_nos": [...]}，
    直接展示原始 JSON 会严重影响可读性，这里统一解析；解析失败则原样返回。
    """
    text = (content or "").strip()
    if not text:
        return text
    if not text.startswith("{"):
        return text
    try:
        payload = extract_json(text)
    except Exception:  # noqa: BLE001
        return text
    if isinstance(payload, dict):
        for key in ("answer", "回答", "content", "text"):
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return text


@router.post("/search", summary="混合检索（返回条款、分数与溯源）")
async def search(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    body: RetrievalRequest = Body(...),
    namespace: Optional[str] = Query(None, description="限定知识库分片，缺省跨全部"),
) -> dict:
    pipeline = RetrievalPipeline(namespace=namespace)
    result = await pipeline.retrieve(
        body.query,
        session,
        kb_ids=body.kb_ids,
        specialty=body.specialty,
        top_k=body.top_k,
        enable_multi_query=body.enable_multi_query,
        enable_kg_expand=body.enable_kg_expand,
    )
    add_audit_log(
        session, AuditAction.RETRIEVAL.value,
        user_id=user.id, username=user.username, ip=client_ip(request),
        object_type="retrieval", detail={"query": body.query[:200], "returned": len(result.clauses)},
    )
    session.commit()
    return ok(result.to_dict(include_content=True))


@router.post("/chat", summary="智能问答（严格基于检索条款并标注引用）")
async def chat(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    body: ChatRequest = Body(...),
    namespace: Optional[str] = Query(None, description="限定知识库分片，缺省跨全部"),
) -> dict:
    started = time.perf_counter()
    pipeline = RetrievalPipeline(namespace=namespace)
    result = await pipeline.retrieve(
        body.query, session, kb_ids=body.kb_ids, specialty=body.specialty, top_k=body.top_k
    )

    # FR-RET-10：无依据时不允许模型自由发挥，直接返回兜底答案
    if result.no_evidence or not result.clauses:
        answer = (
            "未检索到与该问题相关的现行规范条款，无法给出有依据的回答。\n"
            "建议：补充规范来源或放宽专业范围后重试。"
        )
        add_audit_log(
            session, AuditAction.CHAT.value, user_id=user.id, username=user.username,
            ip=client_ip(request), object_type="chat", result="no_evidence",
            detail={"query": body.query[:200]},
        )
        session.commit()
        return ok(
            {
                "answer": answer,
                "no_evidence": True,
                "citations": [],
                "sub_queries": result.sub_queries,
                "latency_ms": int((time.perf_counter() - started) * 1000),
            }
        )

    context, used = assemble_context(result.clauses)
    prompt = RETRIEVAL_ANSWER_USER.format(query=body.query, context=context)
    answer: str
    try:
        response = await get_llm().chat(
            [ChatMessage("system", RETRIEVAL_ANSWER_SYSTEM), ChatMessage("user", prompt)],
            scene="retrieval_answer",
            max_tokens=2048,
        )
        answer = _extract_answer(response.content)
    except Exception as exc:  # noqa: BLE001
        # 降级：模型不可用时返回条款原文摘要，保证可用（FR-AGT-10）
        logger.warning("问答模型调用失败，降级返回条款摘要", extra={"error": str(exc)[:200]})
        answer = "模型服务暂不可用，以下为检索到的规范条款原文：\n\n" + context

    citations = []
    for idx, clause in enumerate(used, start=1):
        citations.append(
            {
                "index": idx,
                "clause_no": clause.citation.clause_no,
                "spec_code": clause.citation.spec_code,
                "spec_name": clause.citation.spec_name,
                "location": clause.citation.render(),
                "chunk_id": clause.chunk_id,
                "relevance_score": round(clause.relevance_score, 4),
                "content": clause.content[:500],
            }
        )

    add_audit_log(
        session, AuditAction.CHAT.value, user_id=user.id, username=user.username,
        ip=client_ip(request), object_type="chat", detail={"query": body.query[:200]},
    )
    session.commit()
    return ok(
        {
            "answer": answer,
            "no_evidence": False,
            "citations": citations,
            "sub_queries": result.sub_queries,
            "latency_ms": int((time.perf_counter() - started) * 1000),
        }
    )
