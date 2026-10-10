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
from app.constants import (
    AuditAction,
    EvalState,
    REVIEWABLE_STATES,
    ReviewStatus,
    overall_verdict_label,
    progress_of,
    review_status_of,
)
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
from app.schemas.api import (
    EvalTaskCreate,
    FeedbackCreate,
    ResumeRequest,
    ReviewDecisionRequest,
)
from app.services.review import decide as review_decide
from app.services.review import report_review_payload

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
        # 进度**由状态派生**，不读库中存储值。
        #
        # 为什么：progress 是 current_state 的纯函数（constants.progress_of），
        # 存两份必然漂移 —— 实测签发后出现「COMPLETED 但 progress=0.9」，
        # 界面上进度条与状态徽标互相矛盾。改为派生后，这一整类不一致不可能再发生
        # （数据库列保留仅为兼容历史数据，不再作为读取来源）。
        "progress": _progress_value(task.current_state),
        "total_tokens": task.total_tokens,
        "guard_reason": task.guard_reason,
        "started_at": task.started_at,
        "finished_at": task.finished_at,
        "created_at": task.created_at,
        # 乐观锁版本号：待复核队列里直接裁定需要回传它，
        # 列表缺这个字段会导致「列表内裁定」拿不到 expected_version（无法防并发）。
        "version": int(task.version),
    }


def _progress_value(state: str) -> float:
    """状态 → 进度。取值非法时返回 0.0（展示层不应因此报错）。"""
    try:
        return progress_of(EvalState(state))
    except ValueError:
        return 0.0


@router.post("/tasks", summary="创建评估任务（API-12）")
def create_task(
    request: Request,
    session: DbSession,
    # 创建任务需要 eval:write。
    # ⚠️ 此前只要求登录（CurrentUser），于是**只读用户也能创建评估任务** ——
    # 虽然执行还需 eval:write 而挡住了一步，但创建本身已属越权：
    # 会污染任务列表、让只读账号产生自己看不懂的数据。
    user: Annotated[User, require_permission("eval:write")],
    body: EvalTaskCreate = Body(...),
) -> dict:
    # 项目授权：非管理员只能把任务建在自己被授权的项目下。
    # 否则会出现「创建后自己都看不到」或「越权写入他人项目」。
    #
    # 同时解决一个「权限给了但流程走不通」的问题：非管理员必须传 project_id，
    # 但工程师未必知道自己被授权了哪些项目（可通过 GET /me/projects 查）。
    # 因此当用户**只被授权一个项目**且未指定时，自动采用该项目 ——
    # 单项目是常见情形，不应要求每次手工指定；多项目时仍要求显式选择以保证隔离。
    if not is_admin(user):
        allowed_projects = sorted(project_ids_of(user))
        if not allowed_projects:
            raise ForbiddenError(
                "当前账号未被授权任何项目，无法创建评估任务。"
                "请联系管理员在「用户与授权」中为你分配项目。"
            )
        if body.project_id is None:
            if len(allowed_projects) == 1:
                body = body.model_copy(update={"project_id": allowed_projects[0]})
            else:
                raise ForbiddenError(
                    f"当前账号被授权 {len(allowed_projects)} 个项目，"
                    "创建任务时必须指定 project_id（可在 GET /api/v1/me/projects 查询）"
                )
        elif int(body.project_id) not in allowed_projects:
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
    # 触发 Agent 执行会调用多次 LLM（真实消耗 Token），必须限定写权限。
    # 此前只做 assert_task_access（归属/项目校验），导致**只读用户看到任务后
    # 也能触发执行** —— 读权限不等于写权限。
    user: Annotated[User, require_permission("eval:write")],
    resume: bool = Query(False, description="是否从最近检查点续跑"),
    force: bool = Query(False, description="已签发的报告需显式 force=true 才能重跑"),
) -> dict:
    task = session.get(EvalTask, task_id)
    if task is None:
        raise NotFoundError(f"任务不存在：{task_id}")
    # 归属校验：非管理员只能访问本人发起或所属项目内的任务
    assert_task_access(user, task)
    if task.current_state in {EvalState.COMPLETED.value, EvalState.CANCELLED.value} and not resume:
        raise ConflictError(f"任务已处于终态 {task.current_state}，如需重跑请使用 resume=true")

    # 已签发的报告是**人工复核的成果**，重跑会重新生成报告并清除签认。
    # 必须显式确认，避免误点「重新执行」就悄悄作废一次人工复核。
    # （此前没有这道防护，实测出现过「签发后重跑 → 新报告凭空继承旧签认」
    #   的矛盾数据，已同时在 runner._upsert_report 修复。）
    if not force and task.report is not None and task.report.is_final:
        raise ConflictError(
            "该报告已由人工复核签发生效，重跑将作废本次签认并需重新复核。"
            "确认重跑请使用 force=true。"
        )

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
    # 同 run：后台执行同样消耗 LLM Token，需写权限
    user: Annotated[User, require_permission("eval:write")],
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
    with_judge: bool = Query(
        False, description="是否附带 Judge 评审摘要（质量评审页需要，默认关闭以保证列表轻量）"
    ),
    with_report: bool = Query(
        False, description="是否附带报告结论与风险等级（评估报告页需要，避免前端逐条查询）"
    ),
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

    # 批量取复核状态，避免逐任务查询（N+1）。
    # 列表上要能区分「待复核」与「已签发生效」—— 未签发的报告不得作为正式依据。
    items = [_task_out(t) for t in rows]
    if rows:
        report_rows = session.execute(
            select(
                EvalReport.task_id,
                EvalReport.human_verdict,
                EvalReport.is_final,
                EvalReport.overall_verdict,
                EvalReport.risk_level,
            ).where(EvalReport.task_id.in_([t.id for t in rows]))
        ).all()
        status_map = {
            task_id: review_status_of(human_verdict, is_final).value
            for task_id, human_verdict, is_final, _, _ in report_rows
        }
        # with_report=true 时附带结论与风险。
        # 为什么需要：评估报告页要显示每份报告的「总体结论 + 风险等级」，
        # 若不给就会退化成前端**逐条查报告接口**（N+1 请求，任务多时明显变慢）。
        # 字段名用 verdict（列表上下文更简洁）；报告详情接口仍是 overall_verdict。
        report_map = {
            task_id: {"verdict": verdict, "risk_level": risk}
            for task_id, _, _, verdict, risk in report_rows
        }
        for item in items:
            tid = uuid.UUID(item["id"])
            item["review_status"] = status_map.get(tid, ReviewStatus.PENDING.value)
            if with_report:
                item.update(report_map.get(tid) or {"verdict": None, "risk_level": None})

        # with_judge=true 时附带 Judge 评审摘要。
        # 为什么需要它：质量评审页的列表要显示「Judge 总分 / 等级 / 是否待复核」，
        # 但这些字段只存在于 JudgeReview 表，列表接口原先不返回 ——
        # 于是那几列**永远是空的**（前端读 row.judge?.xxx 恒为 undefined）。
        # 默认关闭以保证普通列表的响应体轻量。
        if with_judge:
            report_ids = session.execute(
                select(EvalReport.task_id, EvalReport.id).where(
                    EvalReport.task_id.in_([t.id for t in rows])
                )
            ).all()
            report_to_task = {rid: tid for tid, rid in report_ids}
            judge_map: dict[uuid.UUID, dict] = {}
            if report_to_task:
                reviews = (
                    session.execute(
                        select(JudgeReview)
                        .where(JudgeReview.report_id.in_(list(report_to_task)))
                        .order_by(JudgeReview.id.desc())
                    )
                    .scalars()
                    .all()
                )
                # 每个报告只取最新一次评审（已按 id 倒序，首次出现即最新）
                for review in reviews:
                    task_id = report_to_task.get(review.report_id)
                    if task_id is None or task_id in judge_map:
                        continue
                    judge_map[task_id] = {
                        "review_id": review.id,
                        "total_score": float(review.total_score)
                        if review.total_score is not None
                        else None,
                        "grade": review.grade,
                        "needs_human": review.needs_human,
                        "threshold": review.threshold,
                    }
            for item in items:
                item["judge"] = judge_map.get(uuid.UUID(item["id"]))

    return ok(paginate(items, total, page, page_size))


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
    # 人工裁定状态随任务详情返回：详情页需要并列展示「机器结论 / 人工裁定」
    review_info = report_review_payload(task.report)
    if task.report is not None and task.report.reviewed_by:
        reviewer = session.get(User, task.report.reviewed_by)
        if reviewer is not None:
            review_info["reviewed_by_name"] = reviewer.full_name or reviewer.username
    payload["review_status"] = review_info["review_status"]
    payload["review"] = review_info
    payload["version"] = int(task.version)
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

    review_payload = report_review_payload(report)
    if report.reviewed_by:
        reviewer = session.get(User, report.reviewed_by)
        if reviewer is not None:
            review_payload["reviewed_by_name"] = reviewer.full_name or reviewer.username

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
            # 机器结论与人工裁定分栏返回，前端可对比展示（不合并、不覆盖）
            "machine": {
                "overall_verdict": report.overall_verdict,
                "risk_level": report.risk_level,
                "generator_model": report.generator_model,
            },
            "review": review_payload,
            "created_at": report.created_at,
            "judge": judge_payload,
        }
    )


@router.post("/tasks/{task_id}/review", summary="提交人工复核裁定（合格 / 不合格，终审签发）")
def decide_review(
    request: Request,
    task_id: uuid.UUID,
    session: DbSession,
    user: Annotated[User, require_permission("eval:review")],
    background: BackgroundTasks,
    body: ReviewDecisionRequest = Body(...),
) -> dict:
    """人工复核裁定 —— SRS 角色定义「复核与裁定、终审签发」。

    判定**合格** → 报告 ``is_final=True`` 并签发，任务置 ``COMPLETED``，可作正式依据。
    判定**不合格** → 不予签发；``rerun=True`` 驳回重跑（任务回 ``MATCHING``），
    ``rerun=False`` 直接落定不合格。

    机器结论（``overall_verdict``）**不被覆盖**：AI 结论是质量评估的原始证据，
    人工裁定是责任判定，两者并存才能对比出「AI 判合格、人判不合格」这类关键分歧
    （FR-JDG-06 反馈闭环所需样本）。
    """
    task = session.get(EvalTask, task_id)
    if task is None:
        raise NotFoundError(f"任务不存在：{task_id}")
    # 归属校验：非管理员只能裁定本人发起或所属项目内的任务
    assert_task_access(user, task)

    result = review_decide(
        session,
        task=task,
        reviewer=user,
        verdict=body.verdict,
        comment=body.comment,
        rerun=body.rerun,
        revisions=body.corrected_matches,
        expected_version=body.expected_version,
        ip=client_ip(request),
    )
    # 版本号自增，供前端下一次提交做乐观锁
    task.version = int(task.version) + 1
    session.commit()

    # 驳回重跑：与 /resume 同一套后台执行机制，避免接口长时间阻塞
    if result["rerun"]:
        async def _job() -> None:
            from app.db import session_scope

            with session_scope() as inner:
                inner_task = inner.get(EvalTask, task_id)
                if inner_task is not None:
                    await run_evaluation(inner, inner_task, resume=True)

        background.add_task(_job)

    result["version"] = int(task.version)
    return ok(result)


@router.get("/tasks/{task_id}/review", summary="查询人工复核裁定状态")
def get_review(
    task_id: uuid.UUID,
    session: DbSession,
    user: CurrentUser,
) -> dict:
    """查询任务的复核状态：机器结论、人工裁定、签发状态与签认人。"""
    task = session.get(EvalTask, task_id)
    if task is None:
        raise NotFoundError(f"任务不存在：{task_id}")
    assert_task_access(user, task)

    report = session.execute(
        select(EvalReport).where(EvalReport.task_id == task_id)
    ).scalar_one_or_none()
    payload = report_review_payload(report)
    if report is not None and report.reviewed_by:
        reviewer = session.get(User, report.reviewed_by)
        if reviewer is not None:
            payload["reviewed_by_name"] = reviewer.full_name or reviewer.username

    return ok(
        {
            "task_id": str(task.id),
            "report_id": str(report.id) if report else None,
            "current_state": task.current_state,
            "version": int(task.version),
            "machine_verdict": report.overall_verdict if report else None,
            # overall_verdict 可能是「等级」（qualified/excellent）也可能是
            # 「条款判定」（non_compliant/partial），取决于报告怎么生成的 ——
            # 用 overall_verdict_label 兼容两套词汇，不要硬套某一个枚举。
            "machine_verdict_label": overall_verdict_label(
                report.overall_verdict if report else None
            ),
            "risk_level": report.risk_level if report else None,
            **payload,
        }
    )


@router.get("/reviews/pending", summary="待复核队列（后端分页）")
def list_pending_reviews(
    session: DbSession,
    user: Annotated[User, require_permission("eval:review")],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
) -> dict:
    """待复核任务清单。

    判定标准为「**尚无人工裁定**」的已出报告任务（``NEED_HUMAN`` / ``DEGRADED``），
    不是「JudgeReview.needs_human 计数」—— 后者是历史累计（含已处理），
    两者口径不同，曾造成「看板显示 3 个待复核、队列却是空的」的困惑。
    """
    base = (
        select(EvalTask)
        .outerjoin(EvalReport, EvalReport.task_id == EvalTask.id)
        .where(EvalTask.current_state.in_([s.value for s in REVIEWABLE_STATES]))
        .where(EvalReport.human_verdict.is_(None))
    )
    scope = visible_task_filter(user)
    if scope is not None:
        base = base.where(scope)

    total = int(session.execute(select(func.count()).select_from(base.subquery())).scalar() or 0)
    rows = (
        session.execute(
            base.order_by(EvalTask.finished_at.desc().nullslast(), EvalTask.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        .scalars()
        .all()
    )

    items = []
    for task in rows:
        report = session.execute(
            select(EvalReport).where(EvalReport.task_id == task.id)
        ).scalar_one_or_none()
        items.append(
            {
                "task_id": str(task.id),
                "title": task.title,
                "specialty": task.specialty,
                "current_state": task.current_state,
                "guard_reason": task.guard_reason,
                "version": int(task.version),
                "finished_at": task.finished_at,
                "machine_verdict": report.overall_verdict if report else None,
                "risk_level": report.risk_level if report else None,
                "review_status": ReviewStatus.PENDING.value,
            }
        )

    return ok(paginate(items, total=total, page=page, page_size=page_size))


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
    # 复核反馈会写入 HumanFeedback 并影响评估结论，必须限定为具备复核权限的角色。
    # 此前只要求登录，导致只读用户也能提交复核意见、污染质量记录；
    # 与 /tasks/{id}/resume 要求的 eval:review 保持一致。
    user: Annotated[User, require_permission("eval:review")],
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
