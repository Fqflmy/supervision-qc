# -*- coding: utf-8 -*-
"""清理审计/探测过程创建的临时账号与它们产生的测试数据。

这些账号（audit_*、metrics_*、ui_probe_*、probe_*）是权限审计脚本为逐角色
实测而创建的，其发起的探测任务同属测试产物，应一并清理。

保留：admin / engineer / expert / viewer（业务账号）及其真实数据。

删除顺序（外键依赖，从叶到根）：
    judge_score / judge_review -> eval_report
    eval_subtask / match_result / agent_step_log / agent_tool_call -> eval_task
    -> eval_task -> sys_user

用法：
    python scripts/cleanup_audit_users.py --dry-run   # 先看要删什么
    python scripts/cleanup_audit_users.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

PREFIXES = ("audit_", "metrics_", "ui_probe_", "probe_", "um_")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只列出，不删除")
    args = parser.parse_args()

    from sqlalchemy import delete, select

    from app.db.models import (
        AgentStepLog,
        AgentToolCall,
        EvalReport,
        EvalSubtask,
        EvalTask,
        JudgeReview,
        JudgeScore,
        MatchResult,
        User,
    )
    from app.db.session import session_scope

    with session_scope() as session:
        users = list(session.execute(select(User)).scalars().all())
        targets = [u for u in users if u.username.startswith(PREFIXES)]
        target_ids = [u.id for u in targets]

        print(f"=== 当前账号（{len(users)} 个）===")
        for u in users:
            mark = "  → 待清理" if u.id in target_ids else ""
            print(f"  {u.username:24s} role={u.role:12s}{mark}")

        if not targets:
            print()
            print("[OK] 没有需要清理的审计账号")
            return 0

        # 这些账号发起的探测任务
        tasks = (
            list(
                session.execute(
                    select(EvalTask).where(EvalTask.user_id.in_(target_ids))
                ).scalars().all()
            )
            if target_ids
            else []
        )
        task_ids = [t.id for t in tasks]

        print()
        print(f"=== 待清理 {len(targets)} 个账号、{len(task_ids)} 个探测任务 ===")
        for u in targets:
            print(f"  账号 {u.username}")
        for t in tasks[:10]:
            print(f"  任务 {str(t.id)[:8]}  {t.title or '-'}")
        if len(tasks) > 10:
            print(f"  … 其余 {len(tasks) - 10} 个任务")

        if args.dry_run:
            print()
            print("[DRY-RUN] 未执行删除")
            return 0

        deleted = {"reports": 0, "tasks": 0, "users": 0}
        if task_ids:
            reports = list(
                session.execute(
                    select(EvalReport).where(EvalReport.task_id.in_(task_ids))
                ).scalars().all()
            )
            report_ids = [r.id for r in reports]
            if report_ids:
                session.execute(delete(JudgeScore).where(JudgeScore.report_id.in_(report_ids)))
                session.execute(delete(JudgeReview).where(JudgeReview.report_id.in_(report_ids)))
                session.execute(delete(EvalReport).where(EvalReport.id.in_(report_ids)))
            for model in (EvalSubtask, MatchResult, AgentStepLog, AgentToolCall):
                session.execute(delete(model).where(model.task_id.in_(task_ids)))
            session.execute(delete(EvalTask).where(EvalTask.id.in_(task_ids)))
            deleted["reports"] = len(report_ids)
            deleted["tasks"] = len(task_ids)

        session.execute(delete(User).where(User.id.in_(target_ids)))
        deleted["users"] = len(target_ids)

        print()
        print(
            f"[OK] 已删除 账号 {deleted['users']} 个 / 任务 {deleted['tasks']} 个"
            f" / 报告 {deleted['reports']} 个"
        )

    with session_scope() as session:
        remaining = [u.username for u in session.execute(select(User)).scalars().all()]
    print(f"  剩余账号: {remaining}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
