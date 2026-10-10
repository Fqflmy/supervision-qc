# -*- coding: utf-8 -*-
"""人工复核裁定服务（FR-AGT-11 报告签发 / FR-JDG-05 低分拦截 / FR-JDG-06 反馈闭环）。

职责
----
把「人工裁定」这个业务动作集中实现，供 API 与后台任务复用：

1. 校验任务与报告存在、当前状态可复核、乐观锁版本匹配；
2. 应用逐条条款判定修订（可选）；
3. 写入裁定结论（``human_verdict`` / ``review_comment`` / ``reviewed_by`` / ``reviewed_at``）；
4. 判定合格 → ``is_final=True``，任务置 ``COMPLETED``（报告正式出具）；
   判定不合格 → 按 ``rerun`` 决定「驳回重跑」或「直接落定不合格」；
5. 留痕：每次裁定写 1 条 ``human_feedback``（供 FR-JDG-06 样本归档）
   + 1 条 ``audit_log``（供 FR-SYS-03 合规审计）。

为什么不覆盖机器结论
--------------------
``EvalReport.overall_verdict`` 是 AI 生成的原始结论，``human_verdict`` 是人工责任判定。
两者**并存且可对比**：

- 机器结论是模型质量评估的原始证据，FR-JDG-06 的反馈闭环与模型迭代都要用它；
- 「AI 判合格、人判不合格」这类分歧本身是最有价值的训练样本，覆盖即丢失。
"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.constants import (
    HUMAN_VERDICT_LABELS,
    STATE_TRANSITIONS,
    AuditAction,
    EvalState,
    HumanVerdict,
    REVIEW_DECISION_LABELS,
    REVIEW_DECISION_OBJECT_NOTE,
    REVIEW_STATUS_LABELS,
    REVIEW_STATUS_NOTES,
    ReviewStatus,
    progress_of,
    review_decision_label,
    review_decision_of,
    review_status_of,
)
from app.core.errors import ConflictError, NotFoundError, ParamInvalidError
from app.db import add_audit_log
from app.db.models import EvalReport, EvalTask, HumanFeedback, MatchResult, User

#: 可接受人工裁定的任务状态。
#: 除「可复核状态」（NEED_HUMAN / DEGRADED）外，也允许对 **COMPLETED** 任务改判 ——
#: 监理现场常有「复核后发现遗漏」，硬性不可改会逼用户绕过系统另建报告、
#: 反而丢失追溯链。改判会保留历次记录（human_feedback + audit_log）。
REVIEW_ACCEPTING_STATES = {
    EvalState.NEED_HUMAN.value,
    EvalState.DEGRADED.value,
    EvalState.COMPLETED.value,
}


def _utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def report_review_payload(report: EvalReport | None) -> dict[str, Any]:
    """报告的复核相关字段（供接口返回，统一口径）。

    ⚠️ 术语约定（本项目最易混淆处）
    ------------------------------
    本函数返回的是**人工对「AI 报告」的决定**，不是对工程质量的判定。
    因此除 ``human_verdict``（历史字段，保留兼容）外，额外返回：

    - ``review_decision`` / ``review_decision_label``：
      **动作词**（接受报告 / 退回报告）——动作天然绑定对象，
      不会像「复核合格」那样被误读成「工程合格」；
    - ``review_decision_object_note`` / ``review_status_note``：
      对象说明与后果说明，界面须与值同时展示，避免脱离语境。

    工程质量结论在 ``eval_report.overall_verdict``，由 AI 给出，两者**并存不覆盖**。
    """
    if report is None:
        return {
            "human_verdict": None,
            "human_verdict_label": None,
            "review_decision": None,
            "review_decision_label": None,
            "review_decision_object_note": REVIEW_DECISION_OBJECT_NOTE,
            "review_comment": None,
            "reviewed_by": None,
            "reviewed_by_name": None,
            "reviewed_at": None,
            "is_final": False,
            "review_status": ReviewStatus.PENDING.value,
            "review_status_label": REVIEW_STATUS_LABELS[ReviewStatus.PENDING],
            "review_status_note": REVIEW_STATUS_NOTES[ReviewStatus.PENDING],
        }
    status = review_status_of(report.human_verdict, report.is_final)
    return {
        "human_verdict": report.human_verdict,
        "human_verdict_label": HUMAN_VERDICT_LABELS.get(
            HumanVerdict(report.human_verdict), report.human_verdict
        )
        if report.human_verdict
        else None,
        # 展示层决定：动作词 + 对象说明
        "review_decision": review_decision_of(report.human_verdict).value
        if review_decision_of(report.human_verdict)
        else None,
        "review_decision_label": review_decision_label(report.human_verdict),
        "review_decision_object_note": REVIEW_DECISION_OBJECT_NOTE,
        "review_comment": report.review_comment,
        "reviewed_by": report.reviewed_by,
        "reviewed_by_name": None,  # 由调用方按需补充（避免在纯函数里查库）
        "reviewed_at": report.reviewed_at,
        "is_final": report.is_final,
        "review_status": status.value,
        "review_status_label": REVIEW_STATUS_LABELS[status],
        "review_status_note": REVIEW_STATUS_NOTES[status],
    }


def _resolve_task(session: Session, task_id: uuid.UUID) -> EvalTask:
    task = session.get(EvalTask, task_id)
    if task is None:
        raise NotFoundError(f"任务不存在：{task_id}")
    return task


def _load_report(session: Session, task_id: uuid.UUID) -> EvalReport:
    report = session.execute(
        select(EvalReport).where(EvalReport.task_id == task_id)
    ).scalar_one_or_none()
    if report is None:
        raise NotFoundError(f"任务尚无评估报告，无法复核：{task_id}")
    return report


def _apply_match_revisions(
    session: Session, task_id: uuid.UUID, revisions: list[dict[str, Any]]
) -> int:
    """应用人工逐条条款判定修订，返回处理条数。"""
    applied = 0
    for item in revisions:
        match_id = item.get("match_id")
        if match_id is None:
            continue
        match = session.get(MatchResult, int(match_id))
        # 归属校验：只能改本任务的条款，避免越权改他人数据
        if match is None or match.task_id != task_id:
            continue
        if item.get("verdict"):
            match.verdict = item["verdict"]
        if item.get("reasoning"):
            match.reasoning = item["reasoning"]
        if item.get("remediation"):
            match.remediation = item["remediation"]
        applied += 1
    return applied


def _record_review_history(
    session: Session,
    *,
    task_id: uuid.UUID,
    report: EvalReport,
    verdict: str,
    comment: str,
    rerun: bool,
    reviewer: User,
    revisions: list[dict[str, Any]],
    ip: Optional[str] = None,
) -> None:
    """写入裁定留痕：human_feedback（样本归档）+ audit_log（合规审计）。"""
    session.add(
        HumanFeedback(
            task_id=task_id,
            report_id=report.id,
            user_id=getattr(reviewer, "id", None),
            # 整体裁定结论：用枚举值，不再是自由字符串
            action=verdict,
            dimension=None,
            original_value={
                "overall_verdict": report.overall_verdict,
                "risk_level": report.risk_level,
            },
            corrected_value={"human_verdict": verdict, "rerun": rerun},
            comment=comment,
        )
    )

    # 逐条修订也各自留痕，便于回溯「改了哪些条款」
    for item in revisions:
        session.add(
            HumanFeedback(
                task_id=task_id,
                report_id=report.id,
                user_id=getattr(reviewer, "id", None),
                action="correct_match",
                dimension="clause_matching",
                original_value=item.get("original"),
                corrected_value=item,
                comment=comment,
            )
        )

    add_audit_log(
        session,
        AuditAction.REPORT_REVIEW.value,
        user_id=getattr(reviewer, "id", None),
        username=getattr(reviewer, "username", None),
        object_type="eval_report",
        object_id=str(report.id),
        ip=ip,
        detail={
            "verdict": verdict,
            "rerun": rerun,
            "is_final": report.is_final,
            "machine_verdict": report.overall_verdict,
            # 人工与机器结论是否分歧 —— FR-JDG-06 关注的样本
            "diverged": bool(
                report.overall_verdict
                and report.overall_verdict != "unqualified"
                and verdict == HumanVerdict.UNQUALIFIED.value
            ),
            "revision_count": len(revisions),
        },
    )


def decide(
    session: Session,
    *,
    task: EvalTask,
    reviewer: User,
    verdict: str,
    comment: str,
    rerun: bool,
    revisions: list[dict[str, Any]] | None = None,
    expected_version: Optional[int] = None,
    ip: Optional[str] = None,
) -> dict[str, Any]:
    """提交人工复核裁定。

    调用方需已完成权限校验（``eval:review``）与归属校验（``assert_task_access``）；
    本函数只负责业务规则与落库。

    :param verdict: ``qualified`` / ``unqualified``
    :param comment: 裁定依据；判定不合格时**必填**
    :param rerun: 判定不合格时，``True`` 驳回重跑、``False`` 直接落定不合格
    :param revisions: 逐条条款判定修订
    :param expected_version: 乐观锁版本；不匹配返回 409（防两人同时裁定）
    :returns: 裁定后的状态与结论摘要
    """
    revisions = revisions or []

    # ---- 1) 取值校验（枚举，不是自由字符串）----
    try:
        verdict_enum = HumanVerdict(verdict)
    except ValueError:
        raise ParamInvalidError(
            f"verdict 必须是 {[v.value for v in HumanVerdict]} 之一，收到：{verdict!r}"
        ) from None

    if verdict_enum is HumanVerdict.UNQUALIFIED and not (comment or "").strip():
        # 判定不合格必须说明依据 —— 这是质量责任的基本要求
        raise ParamInvalidError("判定不合格必须填写裁定依据（comment）")

    # ---- 2) 状态与并发校验 ----
    if task.current_state not in REVIEW_ACCEPTING_STATES:
        raise ConflictError(
            f"当前状态 {task.current_state} 不接受人工裁定"
            f"（可裁定状态：{sorted(REVIEW_ACCEPTING_STATES)}）"
        )

    if expected_version is not None and int(task.version) != int(expected_version):
        raise ConflictError(
            f"任务已被他人修改（期望版本 {expected_version}，当前 {task.version}），"
            "请刷新后重试"
        )

    report = _load_report(session, task.id)

    # ---- 3) 应用逐条修订 ----
    applied = _apply_match_revisions(session, task.id, revisions)

    # ---- 4) 写入裁定结论 ----
    before = {
        "human_verdict": report.human_verdict,
        "is_final": report.is_final,
        "current_state": task.current_state,
    }

    report.human_verdict = verdict_enum.value
    report.review_comment = comment
    report.reviewed_by = getattr(reviewer, "id", None)
    report.reviewed_at = _utcnow()

    if verdict_enum is HumanVerdict.QUALIFIED:
        # 判定合格 → 签发生效，报告成为正式依据
        report.is_final = True
        _transition(task, EvalState.COMPLETED)
        task.finished_at = task.finished_at or _utcnow()
        is_final = True
    else:
        # 判定不合格 → 不予签发
        report.is_final = False
        is_final = False
        if rerun:
            # 驳回重跑：带着人工修订回到匹配阶段，由调用方调度后台重跑
            report.is_final = False
            _transition(task, EvalState.MATCHING)
        else:
            # 直接落定不合格：任务终结但报告不是正式件
            _transition(task, EvalState.COMPLETED)
            task.finished_at = task.finished_at or _utcnow()

    _record_review_history(
        session,
        task_id=task.id,
        report=report,
        verdict=verdict_enum.value,
        comment=comment,
        rerun=rerun,
        reviewer=reviewer,
        revisions=revisions,
        ip=ip,
    )

    session.flush()

    return {
        "task_id": str(task.id),
        "report_id": str(report.id),
        "human_verdict": verdict_enum.value,
        "human_verdict_label": HUMAN_VERDICT_LABELS[verdict_enum],
        "review_comment": comment,
        "reviewed_by": report.reviewed_by,
        "reviewed_at": report.reviewed_at,
        "is_final": is_final,
        "current_state": task.current_state,
        "rerun": bool(rerun and verdict_enum is HumanVerdict.UNQUALIFIED),
        "revision_count": applied,
        "machine_verdict": report.overall_verdict,
        "before": before,
    }


def _transition(task: EvalTask, target: EvalState) -> None:
    """按状态迁移表置位，非法迁移直接拒绝（不静默改成别的状态）。

    同时同步 ``progress``：进度是**由状态派生**的（见 ``agent.runner._progress_of``），
    而人工裁定也会改变状态 —— 若不同步，会出现
    「current_state=COMPLETED 但 progress=0.9」这类数据不一致
    （实测签发后正是如此：界面上的进度条与状态互相矛盾）。
    """
    current = EvalState(task.current_state)
    if target is current:
        return
    allowed = STATE_TRANSITIONS.get(current, set())
    if target not in allowed:
        raise ConflictError(
            f"状态迁移非法：{current.value} -> {target.value}（允许：{sorted(s.value for s in allowed)}）"
        )
    task.current_state = target.value
    task.progress = progress_of(target)
