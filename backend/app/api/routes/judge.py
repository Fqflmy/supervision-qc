# -*- coding: utf-8 -*-
"""LLM-as-Judge 接口（FR-JDG，SRS 6.2 API-17/18）。"""
from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Body, Query, Request
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, client_ip, require_permission
from app.core.authz import assert_report_access
from app.config import settings
from app.constants import (
    JUDGE_DIMENSION_LABELS,
    JUDGE_GRADE_LABELS,
    AuditAction,
    JudgeDimension,
    EvalState,
    grade_of,
)
from app.core.errors import NotFoundError
from app.core.logging_conf import get_logger
from app.core.response import ok, paginate
from app.db import add_audit_log
from app.db.models import EvalReport, EvalTask, JudgeReview, JudgeScore, User
from app.judge.scorer import evaluate_report, normalize_weights, persist_outcome
from app.schemas.api import JudgeRequest

router = APIRouter(prefix="/judge", tags=["质量评审"])
logger = get_logger(__name__)


def _citations_of(report: EvalReport) -> tuple[list[dict], list[str]]:
    """从报告内容提取引用与结论，用于引用校验（FR-JDG-03）。

    优先使用报告落库时一并保存的 `matches`（Agent 逐条比对结果，含 citation），
    这样能对**全部真实引用**做存在性校验，而不是只校验少量样例。
    """
    content = report.content or {}
    citations: list[dict] = []
    conclusions: list[str] = []
    seen: set[str] = set()

    matches = content.get("matches") if isinstance(content.get("matches"), list) else []
    for item in matches:
        if not isinstance(item, dict):
            continue
        citation = item.get("citation") or {}
        clause_no = item.get("clause_no") or citation.get("clause_no")
        spec_code = item.get("spec_code") or citation.get("spec_code")
        key = f"{spec_code}|{clause_no}|{citation.get('chunk_id')}"
        if not clause_no or key in seen:
            continue
        seen.add(key)
        citations.append(
            {
                "clause_no": clause_no,
                "spec_code": spec_code,
                "chunk_id": citation.get("chunk_id"),
            }
        )
        conclusions.append(str(item.get("reasoning") or item.get("evidence") or "")[:500])

    # 兜底：报告没有落 matches 时，退化为从 evidence_basis 提取
    if not citations:
        for item in content.get("evidence_basis") or []:
            if not isinstance(item, dict):
                continue
            clause_no = item.get("clause_no")
            key = f"{item.get('spec_code')}|{clause_no}"
            if not clause_no or key in seen:
                continue
            seen.add(key)
            citations.append(
                {
                    "clause_no": clause_no,
                    "spec_code": item.get("spec_code"),
                    "chunk_id": item.get("chunk_id"),
                }
            )

    analysis = content.get("analysis") or {}
    if analysis.get("conclusion"):
        conclusions.append(str(analysis["conclusion"])[:500])
    return citations, conclusions


@router.post("/reports/{report_id}/score", summary="触发 LLM-as-Judge 评审（API-17）")
async def score_report(
    request: Request,
    report_id: uuid.UUID,
    session: DbSession,
    user: Annotated[User, require_permission("judge:write")],
    body: JudgeRequest = Body(default=JudgeRequest()),
) -> dict:
    report = session.get(EvalReport, report_id)
    if report is None:
        raise NotFoundError(f"报告不存在：{report_id}")
    # 归属校验：报告可见性由所属任务决定
    assert_report_access(user, session, report)

    citations, conclusions = _citations_of(report)
    outcome = await evaluate_report(
        session,
        report_markdown=report.markdown or "",
        citations=citations,
        conclusions=conclusions,
        weights=body.weights,
        threshold=body.threshold,
        cross_model=body.cross_model,
    )
    review = persist_outcome(session, report, outcome)

    # 低分/分歧时把任务挂到待人工复核（FR-JDG-05）
    task = session.get(EvalTask, report.task_id)
    if task is not None and outcome.needs_human and task.current_state == EvalState.COMPLETED.value:
        task.current_state = EvalState.NEED_HUMAN.value
        task.guard_reason = outcome.comment or "Judge 评分低于阈值，转人工复核"

    add_audit_log(
        session, AuditAction.JUDGE_SCORE.value,
        user_id=getattr(user, "id", None), username=getattr(user, "username", None),
        object_type="eval_report", object_id=str(report_id), ip=client_ip(request),
        detail={"total_score": outcome.total_score, "needs_human": outcome.needs_human},
    )
    session.commit()
    payload = outcome.to_dict()
    payload["review_id"] = review.id
    return ok(payload)


@router.get("/reports/{report_id}/reviews", summary="获取报告的历次评审")
def list_reviews(report_id: uuid.UUID, session: DbSession, user: CurrentUser) -> dict:
    report = session.get(EvalReport, report_id)
    if report is None:
        raise NotFoundError(f"报告不存在：{report_id}")
    # 归属校验：报告可见性由所属任务决定
    assert_report_access(user, session, report)
    reviews = session.execute(
        select(JudgeReview).where(JudgeReview.report_id == report_id).order_by(JudgeReview.id.desc())
    ).scalars().all()
    items = []
    for review in reviews:
        scores = session.execute(
            select(JudgeScore).where(JudgeScore.review_id == review.id)
        ).scalars().all()
        items.append(
            {
                "id": review.id,
                "judge_model": review.judge_model,
                "total_score": float(review.total_score) if review.total_score is not None else None,
                "grade": review.grade,
                "grade_label": JUDGE_GRADE_LABELS.get(grade_of(float(review.total_score or 0)), ""),
                "needs_human": review.needs_human,
                "has_conflict": review.has_conflict,
                "threshold": review.threshold,
                "comment": review.comment,
                "citation_check": review.citation_check,
                "created_at": review.created_at,
                "scores": [
                    {
                        "dimension": s.dimension,
                        "dimension_label": JUDGE_DIMENSION_LABELS.get(
                            JudgeDimension(s.dimension), s.dimension
                        ),
                        "score": float(s.score) if s.score is not None else None,
                        "weight": float(s.weight) if s.weight is not None else None,
                        "comment": s.comment,
                        "is_conflict": s.is_conflict,
                    }
                    for s in scores
                ],
            }
        )
    return ok(items)


@router.get("/dashboard", summary="质量看板（API-18）")
def dashboard(session: DbSession, user: CurrentUser) -> dict:
    review_count = int(session.execute(select(func.count(JudgeReview.id))).scalar() or 0)
    avg_score = session.execute(select(func.avg(JudgeReview.total_score))).scalar()
    needs_human = int(
        session.execute(
            select(func.count(JudgeReview.id)).where(JudgeReview.needs_human.is_(True))
        ).scalar()
        or 0
    )
    conflict_count = int(
        session.execute(
            select(func.count(JudgeReview.id)).where(JudgeReview.has_conflict.is_(True))
        ).scalar()
        or 0
    )

    grade_rows = session.execute(
        select(JudgeReview.grade, func.count(JudgeReview.id)).group_by(JudgeReview.grade)
    ).all()
    grade_distribution = {str(g or "unknown"): int(c) for g, c in grade_rows}

    dim_rows = session.execute(
        select(JudgeScore.dimension, func.avg(JudgeScore.score)).group_by(JudgeScore.dimension)
    ).all()
    dimension_averages = {
        JUDGE_DIMENSION_LABELS.get(JudgeDimension(d), d): round(float(v or 0), 2) for d, v in dim_rows
    }

    hallucination_total = 0
    for review in session.execute(select(JudgeReview.citation_check)).all():
        check = review[0] or {}
        hallucination_total += int(check.get("hallucinations") or 0)

    return ok(
        {
            "review_count": review_count,
            "avg_total_score": round(float(avg_score or 0), 2),
            "needs_human_count": needs_human,
            "conflict_count": conflict_count,
            "grade_distribution": grade_distribution,
            "dimension_averages": dimension_averages,
            "hallucination_total": hallucination_total,
            "threshold": settings.judge_threshold,
            # normalize_weights 返回的键已是字符串（JudgeDimension.value）
            "weights": normalize_weights(),
        }
    )
