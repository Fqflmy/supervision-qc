# -*- coding: utf-8 -*-
"""评估任务接口（FR-AGT，SRS 6.2 API-12~16）。"""
from __future__ import annotations

import uuid
from typing import Annotated, Optional

from fastapi import APIRouter, BackgroundTasks, Body, Query, Request
from sqlalchemy import func, select
from sqlalchemy.orm import selectinload

from app.agent.runner import run_evaluation
from app.api.deps import CurrentUser, DbSession, client_ip, require_permission
from app.core.authz import (
    assert_kb_query_allowed,
    assert_report_access,
    assert_task_access,
    is_admin,
    project_ids_of,
    visible_task_filter,
)
from app.constants import AuditAction, EvalState, REVIEWABLE_STATES
from app.core.errors import ConflictError, ForbiddenError, NotFoundError
from app.core.logging_conf import get_logger
from app.core.response import ok, paginate
from app.db import add_audit_log, utcnow
from app.db.models import (
    AgentStepLog,
    EvalReport,
    EvalTask,
    HumanFeedback,
    JudgeReview,
    JudgeScore,
    MatchResult,
    User,
)
from app.schemas.api import EvalTaskCreate, FeedbackCreate, ResumeRequest

router = APIRouter(prefix="/eval", tags=["评估任务"])
logger = get_logger(__name__)


def _task_out(task: EvalTask) -> dict:
    return {
        "id": str(task.id),
        "project_id": task.project_id,
        "eval_type": task.eval_type,
        "specialty": task.specialty,
        "title": task.title,
        "current_state": task.current_state,
        "current_step": task.current_step,
        "iteration_count": task.iteration_count,
        "no_progress_rounds": task.no_progress_rounds,
        "progress": task.progress,
        "total_tokens": task.total_tokens,
        "guard_reason": task.guard_reason,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "created_at": task.created_at,
    }


@router.post("/tasks", summary="创建评估任务（API-12）")
def create_task(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    body: EvalTaskCreate = Body(...),
) -> dict:
    # 项目授权：非管理员只能把任务建在自己被授权的项目下。
    # 否则会出现「创建后自己都看不到」或「越权写入他人项目」。
    if not is_admin(user):
        allowed_projects = project_ids_of(user)
        if body.project_id is None:
            raise ForbiddenError("非管理员创建任务必须指定 project_id（且需在授权项目范围内）")
        if int(body.project_id) not in allowed_projects:
            logger.warning(
                "越权创建任务被拒绝",
                extra={"user_id": user.id, "project_id": body.project_id},
            )
            raise ForbiddenError(f"无权在项目 {body.project_id} 下创建评估任务")

    # 知识库授权：任务指定的检索范围不能超出用户可访问的知识库
    if body.kb_ids:
        assert_kb_query_allowed(session, user, requested_kb_ids=body.kb_ids)

    options = dict(body.options or {})
    if body.kb_ids:
        options["kb_ids"] = body.kb_ids
    payload = body.object.model_dump()
    payload["specialty"] = body.specialty
    payload["eval_type"] = body.eval_type

    task = EvalTask(
        project_id=body.project_id,
        user_id=user.id,
        eval_type=body.eval_type,
        specialty=body.specialty,
        title=body.title or f"{(body.object.part or '评估对象')}质量评估",
        input_payload=payload,
        options=options,
        current_state=EvalState.PENDING.value,
    )
    session.add(task)
    session.flush()
    add_audit_log(
        session, AuditAction.EVAL_CREATE.value,
        user_id=user.id, username=user.username, object_type="eval_task",
        object_id=str(task.id), ip=client_ip(request), detail={"part": body.object.part},
    )
    session.commit()
    return ok(
        {
            "task_id": str(task.id),
            "current_state": task.current_state,
            "thread_id": task.checkpoint_thread_id,
            "message": "任务已创建",
        }
    )


@router.post("/tasks/{task_id}/run", summary="同步执行评估任务（Agent 五阶段）")
async def run_task(
    request: Request,
    task_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
    resume: bool = Query(False, description="是否从最近检查点续跑"),
) -> dict:
    task = session.get(EvalTask, task_id)
    if task is None:
        raise NotFoundError(f"任务不存在：{task_id}")
    # 归属校验：非管理员只能访问本人发起或所属项目内的任务
    assert_task_access(user, task)
    if task.current_state in {EvalState.COMPLETED.value, EvalState.CANCELLED.value} and not resume:
        raise ConflictError(f"任务已处于终态 {task.current_state}，如需重跑请使用 resume=true")

    # 说明：本请求会在持有会话的情况下执行耗时数十秒到数分钟的 Agent（多次 LLM 调用），
    # 期间连接可能被服务端回收（空闲超时/连接池回收/网络抖动）。落库阶段已内置
    # 「丢弃坏连接 + 重放」的自愈逻辑（见 agent/runner.py），无需在此特殊处理。
    # （不要在此处 session.close()：会让 task 对象脱离会话，导致状态写不进库。）
    result = await run_evaluation(session, task, resume=resume)
    add_audit_log(
        session, AuditAction.EVAL_RESUME.value if resume else AuditAction.EVAL_CREATE.value,
        user_id=user.id, username=user.username, object_type="eval_task", object_id=str(task_id),
        ip=client_ip(request),
        detail={"state": result["current_state"], "iterations": result["iteration_count"]},
    )
    session.commit()
    result.pop("markdown", None)
    return ok(result)


@router.post("/tasks/{task_id}/submit", summary="异步提交评估任务（后台执行，立即返回）")
def submit_task(
    request: Request,
    task_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
    background: BackgroundTasks,
) -> dict:
    task = session.get(EvalTask, task_id)
    if task is None:
        raise NotFoundError(f"任务不存在：{task_id}")
    # 归属校验：非管理员只能访问本人发起或所属项目内的任务
    assert_task_access(user, task)
    if task.current_state in {EvalState.PLANNING.value, EvalState.RETRIEVING.value, EvalState.MATCHING.value}:
        raise ConflictError(f"任务正在执行中：{task.current_state}")

    task.current_state = EvalState.PENDING.value
    session.commit()

    async def _job() -> None:
        from app.db import session_scope

        with session_scope() as inner:
            inner_task = inner.get(EvalTask, task_id)
            if inner_task is None:
                return
            try:
                await run_evaluation(inner, inner_task)
            except Exception as exc:  # noqa: BLE001
                logger.error("后台评估任务失败", extra={"task_id": str(task_id), "error": str(exc)[:300]})
                inner_task.current_state = EvalState.FAILED.value
                inner_task.error_state = {"type": "background", "message": str(exc)[:500]}

    background.add_task(_job)
    return ok({"task_id": str(task_id), "current_state": EvalState.PENDING.value, "message": "已提交后台执行"})


@router.get("/tasks", summary="任务列表")
def list_tasks(
    session: DbSession,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    state: Optional[str] = None,
    mine: bool = Query(False, description="仅看我发起的任务"),
) -> dict:
    # 默认只返回「当前用户可访问」的任务（本人发起 或 所属项目内）。
    # 修复前这里是默认返回全部用户、全部项目的任务，属越权。
    stmt = select(EvalTask)

    scope = visible_task_filter(user)
    if scope is not None:
        stmt = stmt.where(scope)

    if state:
        stmt = stmt.where(EvalTask.current_state == state)
    if mine:
        stmt = stmt.where(EvalTask.user_id == user.id)
    stmt = stmt.order_by(EvalTask.created_at.desc())

    page = max(1, page)
    page_size = min(100, max(1, page_size))
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int(session.execute(count_stmt).scalar() or 0)
    rows = session.execute(stmt.limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return ok(paginate([_task_out(t) for t in rows], total, page, page_size))


@router.get("/tasks/{task_id}", summary="查询任务状态与状态机快照（API-13）")
def get_task(task_id: uuid.UUID, session: DbSession, user: CurrentUser) -> dict:
    task = session.execute(
        select(EvalTask)
        .options(
            selectinload(EvalTask.subtasks),
            selectinload(EvalTask.matches),
            selectinload(EvalTask.steps),
            selectinload(EvalTask.report),
        )
        .where(EvalTask.id == task_id)
    ).scalars().first()
    if task is None:
        raise NotFoundError(f"任务不存在：{task_id}")
    # 归属校验：非管理员只能查看本人发起或所属项目内的任务
    assert_task_access(user, task)

    review = None
    if task.report is not None:
        review = session.execute(
            select(JudgeReview)
            .where(JudgeReview.report_id == task.report.id)
            .order_by(JudgeReview.id.desc())
        ).scalars().first()

    payload = _task_out(task)
    payload.update(
        {
            "input_payload": task.input_payload,
            "options": task.options,
            "state_snapshot": task.state_snapshot,
            "error_state": task.error_state,
            "subtasks": [
                {
                    "id": s.id,
                    "seq": s.seq,
                    "name": s.name,
                    "criterion": s.criterion,
                    "required_evidence": s.required_evidence,
                    "specialty": s.specialty,
                    "query": s.query,
                    "status": s.status,
                }
                for s in sorted(task.subtasks, key=lambda x: x.seq)
            ],
            "matches": [
                {
                    "id": m.id,
                    "clause_no": m.clause_no,
                    "spec_code": m.spec_code,
                    "spec_name": m.spec_name,
                    "verdict": m.verdict,
                    "confidence": float(m.confidence) if m.confidence is not None else None,
                    "relevance_score": float(m.relevance_score) if m.relevance_score is not None else None,
                    "evidence": m.evidence,
                    "reasoning": m.reasoning,
                    "risk_level": m.risk_level,
                    "remediation": m.remediation,
                    "citation_json": m.citation_json,
                }
                for m in task.matches
            ],
            "steps": [
                {
                    "id": s.id,
                    "step": s.step,
                    "seq": s.seq,
                    "status": s.status,
                    "duration_ms": s.duration_ms,
                    "token_used": s.token_used,
                    "iteration": s.iteration,
                    "retry_count": s.retry_count,
                    "output_digest": s.output_digest,
                    "error": s.error,
                }
                for s in sorted(task.steps, key=lambda x: x.seq)
            ],
            "report_id": str(task.report.id) if task.report is not None else None,
            "judge": (
                {
                    "review_id": review.id,
                    "total_score": float(review.total_score) if review.total_score is not None else None,
                    "grade": review.grade,
                    "needs_human": review.needs_human,
                    "has_conflict": review.has_conflict,
                    "comment": review.comment,
                }
                if review is not None
                else None
            ),
        }
    )
    return ok(payload)


@router.get("/tasks/{task_id}/report", summary="获取评估报告（API-16）")
def get_report(
    task_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
    format: str = Query("json", pattern="^(json|markdown)$"),
) -> dict:
    report = session.execute(
        select(EvalReport).where(EvalReport.task_id == task_id)
    ).scalars().first()
    if report is None:
        raise NotFoundError("该任务还没有生成报告，请先执行评估")
    # 归属校验：报告的可见性由所属任务决定（防止拿到 task_id 就读他人报告）
    assert_report_access(user, session, report)

    if format == "markdown":
        return ok({"report_id": str(report.id), "markdown": report.markdown or ""})

    review = session.execute(
        select(JudgeReview).where(JudgeReview.report_id == report.id).order_by(JudgeReview.id.desc())
    ).scalars().first()
    judge_payload = None
    if review is not None:
        scores = session.execute(
            select(JudgeScore).where(JudgeScore.review_id == review.id)
        ).scalars().all()
        judge_payload = {
            "review_id": review.id,
            "total_score": float(review.total_score) if review.total_score is not None else None,
            "grade": review.grade,
            "needs_human": review.needs_human,
            "has_conflict": review.has_conflict,
            "threshold": review.threshold,
            "comment": review.comment,
            "citation_check": review.citation_check,
            "scores": [
                {
                    "dimension": s.dimension,
                    "score": float(s.score) if s.score is not None else None,
                    "weight": float(s.weight) if s.weight is not None else None,
                    "comment": s.comment,
                    "is_conflict": s.is_conflict,
                }
                for s in scores
            ],
        }

    return ok(
        {
            "id": str(report.id),
            "task_id": str(report.task_id),
            "conclusion": report.conclusion,
            "overall_verdict": report.overall_verdict,
            "risk_level": report.risk_level,
            "summary": report.summary,
            "markdown": report.markdown,
            "content": report.content,
            "basis_count": report.basis_count,
            "non_compliance_count": report.non_compliance_count,
            "generator_model": report.generator_model,
            "is_final": report.is_final,
            "created_at": report.created_at,
            "judge": judge_payload,
        }
    )


@router.post("/tasks/{task_id}/resume", summary="人工介入后继续执行（API-15）")
def resume_task(
    request: Request,
    task_id: uuid.UUID,
    session: DbSession,
    user: Annotated[User, require_permission("eval:review")],
    background: BackgroundTasks,
    body: ResumeRequest = Body(...),
) -> dict:
    task = session.get(EvalTask, task_id)
    if task is None:
        raise NotFoundError(f"任务不存在：{task_id}")
    # 归属校验：非管理员只能访问本人发起或所属项目内的任务
    assert_task_access(user, task)
    if task.current_state not in {s.value for s in REVIEWABLE_STATES}:
        raise ConflictError(f"当前状态 {task.current_state} 不需要人工介入")

    feedback_count = 0
    if body.corrected_matches:
        for item in body.corrected_matches:
            session.add(
                HumanFeedback(
                    task_id=task_id,
                    user_id=getattr(user, "id", None),
                    action="correct_match",
                    dimension="clause_matching",
                    original_value=item.get("original"),
                    corrected_value=item,
                    comment=body.comment,
                )
            )
            feedback_count += 1
        # 人工修订写回 match_result
        for item in body.corrected_matches:
            match_id = item.get("match_id")
            if match_id is None:
                continue
            match = session.get(MatchResult, int(match_id))
            if match is None or match.task_id != task_id:
                continue
            if item.get("verdict"):
                match.verdict = item["verdict"]
            if item.get("reasoning"):
                match.reasoning = item["reasoning"]
            if item.get("remediation"):
                match.remediation = item["remediation"]

    if body.action == "cancel":
        task.current_state = EvalState.CANCELLED.value
        task.finished_at = utcnow()
        session.commit()
        return ok({"task_id": str(task_id), "current_state": task.current_state, "feedback": feedback_count})

    task.current_state = EvalState.PENDING.value
    session.commit()

    async def _job() -> None:
        from app.db import session_scope

        with session_scope() as inner:
            inner_task = inner.get(EvalTask, task_id)
            if inner_task is not None:
                await run_evaluation(inner, inner_task, resume=True)

    background.add_task(_job)
    add_audit_log(
        session, AuditAction.EVAL_RESUME.value,
        user_id=getattr(user, "id", None), username=getattr(user, "username", None),
        object_type="eval_task", object_id=str(task_id), ip=client_ip(request),
        detail={"feedback": feedback_count},
    )
    session.commit()
    return ok(
        {
            "task_id": str(task_id),
            "current_state": EvalState.PENDING.value,
            "feedback": feedback_count,
            "message": "已提交重新执行",
        }
    )


@router.post("/feedback", summary="专家复核反馈（FR-JDG-06 反馈闭环）")
def create_feedback(
    session: DbSession,
    user: CurrentUser,
    body: FeedbackCreate = Body(...),
) -> dict:
    feedback = HumanFeedback(
        task_id=uuid.UUID(body.task_id) if body.task_id else None,
        report_id=uuid.UUID(body.report_id) if body.report_id else None,
        user_id=user.id,
        action=body.action,
        dimension=body.dimension,
        original_value=body.original_value,
        corrected_value=body.corrected_value,
        comment=body.comment,
    )
    session.add(feedback)
    session.commit()
    return ok({"feedback_id": feedback.id})
