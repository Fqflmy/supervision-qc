# -*- coding: utf-8 -*-
"""清理测试/验收产生的账号、项目与任务。

测试用例会在库中留下：
- 专用账号：authz_* / lbl_* / rev_* / audit_* / metrics_* / um_* / ui_probe_* / probe_*
- 测试项目：AUTHZ-* / REV-*
- 测试任务：标题含「测试」「越权」「验收」等前缀的

这些都不是业务数据，长期累积会让「数据盘点」失真
（实测曾出现「用户列表 9 行、项目一堆 AUTHZ-*」）。

用法（容器内）：
    python scripts/cleanup_test_data.py --dry-run
    python scripts/cleanup_test_data.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

USER_PREFIXES = ("authz_", "lbl_", "rev_", "audit_", "metrics_", "um_", "ui_probe_", "probe_")
PROJECT_CODES = ("AUTHZ-", "REV-")
TASK_PREFIXES = (
    "越权测试",
    "状态一致性测试",
    "人工复核测试",
    "端到端验收",
    "界面验收",
    "验收·",
    "示例·",  # 示例任务按需保留；加 --keep-demo 可跳过
)
#: 这些账号是种子业务账号，绝不能删
KEEP_USERS = {"admin", "engineer", "expert", "kb_manager", "viewer"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--keep-demo", action="store_true", help="保留「示例·」开头的任务")
    args = parser.parse_args()

    from sqlalchemy import delete, select

    from app.db.models import (
        AgentStepLog,
        AgentToolCall,
        EvalReport,
        EvalSubtask,
        EvalTask,
        HumanFeedback,
        JudgeReview,
        JudgeScore,
        MatchResult,
        Project,
        User,
    )
    from app.db.session import session_scope

    task_prefixes = tuple(p for p in TASK_PREFIXES if not (args.keep_demo and p == "示例·"))

    with session_scope() as session:
        users = session.execute(select(User)).scalars().all()
        victims_users = [
            u for u in users if u.username.startswith(USER_PREFIXES) and u.username not in KEEP_USERS
        ]
        projects = session.execute(select(Project)).scalars().all()
        victims_projects = [p for p in projects if (p.code or "").startswith(PROJECT_CODES)]
        tasks = session.execute(select(EvalTask)).scalars().all()
        victims_tasks = [
            t for t in tasks if any((t.title or "").startswith(p) for p in task_prefixes)
        ]

        print("=== 待清理 ===")
        print(f"  账号 {len(victims_users)}：{[u.username for u in victims_users]}")
        print(f"  项目 {len(victims_projects)}：{[p.code for p in victims_projects]}")
        print(f"  任务 {len(victims_tasks)}：{[str(t.title)[:20] for t in victims_tasks]}")
        print()
        print("=== 保留 ===")
        print(f"  账号：{sorted(KEEP_USERS)}")
        print(f"  项目：{[p.code for p in projects if p not in victims_projects]}")
        print(
            f"  任务：{[str(t.title)[:24] for t in tasks if t not in victims_tasks]}"
        )

        if args.dry_run:
            print()
            print("[DRY-RUN] 未做修改")
            return 0

        # 1) 任务及其关联数据（从叶到根）
        task_ids = [t.id for t in victims_tasks]
        if task_ids:
            reports = (
                session.execute(
                    select(EvalReport).where(EvalReport.task_id.in_(task_ids))
                )
                .scalars()
                .all()
            )
            report_ids = [r.id for r in reports]
            if report_ids:
                session.execute(delete(JudgeScore).where(JudgeScore.report_id.in_(report_ids)))
                session.execute(delete(JudgeReview).where(JudgeReview.report_id.in_(report_ids)))
                session.execute(delete(EvalReport).where(EvalReport.id.in_(report_ids)))
            for model in (HumanFeedback, EvalSubtask, MatchResult, AgentStepLog, AgentToolCall):
                session.execute(delete(model).where(model.task_id.in_(task_ids)))
            session.execute(delete(EvalTask).where(EvalTask.id.in_(task_ids)))

        # 2) 测试账号（先解绑其项目引用不必要，project_ids 只是 JSON 数组）
        if victims_users:
            session.execute(delete(User).where(User.id.in_([u.id for u in victims_users])))

        # 3) 测试项目（须在任务删除之后，否则外键报错）
        if victims_projects:
            session.execute(delete(Project).where(Project.id.in_([p.id for p in victims_projects])))

        print()
        print(
            f"[OK] 已清理 账号 {len(victims_users)} / 项目 {len(victims_projects)} "
            f"/ 任务 {len(victims_tasks)}"
        )

    with session_scope() as session:
        print()
        print("=== 清理后 ===")
        for name, model in [
            ("账号", User),
            ("项目", Project),
            ("任务", EvalTask),
            ("报告", EvalReport),
        ]:
            rows = session.execute(select(model)).scalars().all()
            print(f"  {name} {len(rows)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
