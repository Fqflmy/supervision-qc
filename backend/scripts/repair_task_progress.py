# -*- coding: utf-8 -*-
"""修复 `progress` 与 `current_state` 不一致的存量任务。

背景
----
`progress` 是由状态派生的离散值（`constants.progress_of`）。
它原先只在 Agent 执行路径写入（`runner._apply_task_fields`），
而**人工裁定**也会改变状态（`NEED_HUMAN → COMPLETED`）却不同步 progress，
于是留下「current_state=COMPLETED 但 progress=0.9」这类矛盾数据 ——
界面上的进度条与状态互相打架（实测签发后正是如此）。

代码已修（`services/review._transition` 同步 progress），
本脚本用于**修复修复之前已产生的存量数据**。

用法（容器内）：
    python scripts/repair_task_progress.py --dry-run   # 先看要修哪些
    python scripts/repair_task_progress.py
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="只列出，不修改")
    args = parser.parse_args()

    from sqlalchemy import select

    from app.constants import EvalState, progress_of
    from app.db.models import EvalTask
    from app.db.session import session_scope

    fixed = 0
    with session_scope() as session:
        tasks = session.execute(select(EvalTask)).scalars().all()
        print(f"=== 检查 {len(tasks)} 个任务的 progress 一致性 ===")
        for task in tasks:
            try:
                expected = progress_of(EvalState(task.current_state))
            except ValueError:
                continue
            actual = float(task.progress or 0.0)
            if abs(actual - expected) < 1e-6:
                continue
            fixed += 1
            print(
                f"  {str(task.title)[:26]:28s} state={task.current_state:12s} "
                f"progress {actual} -> {expected}"
            )
            if not args.dry_run:
                task.progress = expected

    print()
    if fixed == 0:
        print("[OK] 没有不一致的数据")
        return 0
    if args.dry_run:
        print(f"[DRY-RUN] 待修复 {fixed} 个（未修改）")
        return 0
    print(f"[OK] 已修复 {fixed} 个任务的 progress")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
