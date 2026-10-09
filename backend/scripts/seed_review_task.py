# -*- coding: utf-8 -*-
"""在 **api 容器内**创建一个待复核任务，供界面验收使用。

⚠️ 为什么必须跑在容器内
----------------------
全栈 api 连接的是 compose 网络内的 ``postgres:5432``，
而宿主机 ``127.0.0.1:5432`` 是 infra 数据库 —— **两个不同的库**。
在宿主机造的任务，浏览器里根本看不到（这个坑实际踩到过，
表现为「脚本说造好了，界面却是空的」）。

用法（在 api 容器内）：
    python scripts/seed_review_task.py            # 打印 task_id
    python scripts/seed_review_task.py --cleanup  # 清理历次验收数据
"""
from __future__ import annotations

import argparse
import sys
import uuid
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: 验收数据的标题前缀，便于识别与清理
TITLE_PREFIX = "验收·"


def create(state: str = "NEED_HUMAN", title: str | None = None) -> str:
    from app.db.models import EvalReport, EvalTask, Project, User
    from app.db.session import session_scope

    with session_scope() as session:
        project = session.query(Project).order_by(Project.id).first()
        if project is None:
            raise RuntimeError("库中没有项目，请先运行 seed_data.py")
        owner = session.query(User).filter(User.username == "engineer").one_or_none()
        if owner is None:
            raise RuntimeError("缺少 engineer 账号，请先运行 seed_data.py")

        task = EvalTask(
            id=uuid.uuid4(),
            project_id=project.id,
            user_id=owner.id,
            title=title or f"{TITLE_PREFIX}人工复核裁定",
            eval_type="inspection_lot",
            specialty="结构工程",
            current_state=state,
            iteration_count=2,
            version=1,
        )
        session.add(task)
        session.flush()
        session.add(
            EvalReport(
                id=uuid.uuid4(),
                task_id=task.id,
                conclusion="混凝土强度满足设计要求，但存在一处引用条款版本过期。",
                # 机器判「合格」—— 便于验证人工判不合格时的分歧标记
                overall_verdict="qualified",
                risk_level="medium",
                summary="验收用报告摘要",
                content={"matches": []},
                basis_count=3,
                non_compliance_count=1,
                generator_model="deepseek-chat",
                human_verdict=None,
                is_final=False,
            )
        )
        return str(task.id)


def cleanup() -> int:
    """清理历次验收产生的任务与报告（不碰真实数据）。"""
    from sqlalchemy import delete, select

    from app.db.models import EvalReport, EvalTask, HumanFeedback
    from app.db.session import session_scope

    with session_scope() as session:
        rows = (
            session.execute(
                select(EvalTask).where(EvalTask.title.like(f"{TITLE_PREFIX}%"))
            )
            .scalars()
            .all()
        )
        ids = [t.id for t in rows]
        if not ids:
            print("  没有验收数据需要清理")
            return 0
        session.execute(delete(HumanFeedback).where(HumanFeedback.task_id.in_(ids)))
        session.execute(delete(EvalReport).where(EvalReport.task_id.in_(ids)))
        session.execute(delete(EvalTask).where(EvalTask.id.in_(ids)))
        print(f"  已清理 {len(ids)} 个验收任务（含报告与反馈）")
        return len(ids)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true", help="清理历次验收数据")
    parser.add_argument("--state", default="NEED_HUMAN", help="任务初始状态")
    args = parser.parse_args()

    if args.cleanup:
        cleanup()
        return 0

    task_id = create(args.state)
    # 只打印 ID，便于脚本用 tail -1 获取
    print(task_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
