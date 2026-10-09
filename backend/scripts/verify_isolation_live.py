# -*- coding: utf-8 -*-
"""验证项目隔离在真实账号上生效，并清理测试残留。

为什么需要
----------
单元测试用替身对象验证逻辑，但**真实账号 + 真实数据**才能发现配置类问题
（例如某个角色实际拿不到权限、某个过滤条件写反了）。

本脚本以 admin / engineer / viewer 三个真实账号登录，对比它们看到的
任务、知识库、项目范围是否符合预期。

用法：
    python scripts/verify_isolation_live.py
    python scripts/verify_isolation_live.py --cleanup   # 顺便清理测试残留数据
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

#: 测试脚手架产生的项目编码前缀（清理目标）
TEST_PREFIXES = ("ISO-A-", "ISO-B-", "UM-P1-", "UM-P2-")


def cleanup_test_data(session) -> int:
    """删除测试残留项目与用户（仅限测试前缀，绝不动 DEMO-001）。

    必须按外键依赖「从叶到根」删除，否则会 ForeignKeyViolation：
        judge_score/judge_review -> eval_report
        doc_chunk -> doc_version -> spec_doc -> knowledge_base
        eval_subtask/match_result/agent_step_log/agent_tool_call -> eval_task
    """
    from sqlalchemy import delete, or_, select

    from app.db.models import (
        AgentStepLog,
        AgentToolCall,
        DocChunk,
        DocVersion,
        EvalReport,
        EvalSubtask,
        EvalTask,
        JudgeReview,
        JudgeScore,
        KnowledgeBase,
        MatchResult,
        Project,
        SpecDoc,
        User,
    )

    projects = (
        session.query(Project)
        .filter(or_(*[Project.code.like(f"{p}%") for p in TEST_PREFIXES]))
        .all()
    )
    project_ids = [p.id for p in projects]
    if not project_ids:
        return 0

    # 测试用户（先记下用户名，稍后删）
    test_usernames = [
        u.username
        for u in session.query(User).all()
        if u.username.startswith(("alice_", "bob_", "um_"))
    ]

    # 1) 任务相关
    task_ids = [
        t.id for t in session.query(EvalTask).filter(EvalTask.project_id.in_(project_ids)).all()
    ]
    if task_ids:
        report_ids = [
            r.id for r in session.query(EvalReport).filter(EvalReport.task_id.in_(task_ids)).all()
        ]
        if report_ids:
            session.execute(delete(JudgeScore).where(JudgeScore.report_id.in_(report_ids)))
            session.execute(delete(JudgeReview).where(JudgeReview.report_id.in_(report_ids)))
            session.execute(delete(EvalReport).where(EvalReport.id.in_(report_ids)))
        for model in (EvalSubtask, MatchResult, AgentStepLog, AgentToolCall):
            session.execute(delete(model).where(model.task_id.in_(task_ids)))
        session.execute(delete(EvalTask).where(EvalTask.id.in_(task_ids)))

    # 2) 知识库相关：doc_chunk -> doc_version -> spec_doc -> knowledge_base
    kb_ids = [
        k.id
        for k in session.query(KnowledgeBase).filter(KnowledgeBase.project_id.in_(project_ids)).all()
    ]
    if kb_ids:
        doc_ids = [d.id for d in session.query(SpecDoc).filter(SpecDoc.kb_id.in_(kb_ids)).all()]
        if doc_ids:
            version_ids = [
                v.id
                for v in session.query(DocVersion).filter(DocVersion.doc_id.in_(doc_ids)).all()
            ]
            if version_ids:
                session.execute(delete(DocChunk).where(DocChunk.version_id.in_(version_ids)))
                session.execute(delete(DocVersion).where(DocVersion.id.in_(version_ids)))
            session.execute(delete(SpecDoc).where(SpecDoc.id.in_(doc_ids)))
        session.execute(delete(KnowledgeBase).where(KnowledgeBase.id.in_(kb_ids)))

    # 3) 用户与项目
    if test_usernames:
        session.execute(delete(User).where(User.username.in_(test_usernames)))
    session.execute(delete(Project).where(Project.id.in_(project_ids)))
    return len(project_ids)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true", help="清理测试残留项目与用户")
    args = parser.parse_args()

    from fastapi.testclient import TestClient

    from app.db.session import session_scope
    from app.main import app

    if args.cleanup:
        with session_scope() as session:
            removed = cleanup_test_data(session)
        print(f"[0] 已清理测试残留项目 {removed} 个")
        print()

    failures: list[str] = []
    with TestClient(app) as client:
        # 找到示例项目 ID
        with session_scope() as session:
            from app.db.models import Project

            demo = session.query(Project).filter(Project.code == "DEMO-001").one_or_none()
            demo_id = demo.id if demo else None
        print(f"    示例项目 DEMO-001 id={demo_id}")

        accounts = [("admin", "Admin@12345"), ("engineer", "Admin@12345"), ("viewer", "Admin@12345")]
        print()
        print(f"{'账号':10s} {'角色':10s} {'项目':16s} {'任务数':>6s} {'知识库':>6s} {'提示':>4s}")
        print("-" * 62)
        for username, password in accounts:
            login = client.post(
                "/api/v1/auth/login", json={"username": username, "password": password}
            )
            if login.status_code != 200 or login.json().get("code") != 0:
                failures.append(f"{username} 登录失败：{login.status_code}")
                print(f"{username:10s} 登录失败 {login.status_code}")
                continue
            headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

            scope = client.get("/api/v1/me/scope", headers=headers).json()["data"]
            tasks = client.get("/api/v1/eval/tasks?page_size=100", headers=headers).json()["data"]
            kbs = client.get("/api/v1/kb", headers=headers).json()["data"]
            task_count = tasks.get("total", len(tasks.get("items", [])))

            print(
                f"{username:10s} {scope['role']:10s} {str(scope['project_ids']):16s} "
                f"{task_count:6d} {len(kbs):6d} {'有' if scope.get('warning') else '无':>4s}"
            )

            if username == "admin":
                if scope["is_admin"] is not True:
                    failures.append("admin 应被识别为管理员")
                if scope.get("warning"):
                    failures.append("admin 不应有缺授权提示")
            else:
                if scope["is_admin"] is not False:
                    failures.append(f"{username} 不应被识别为管理员")
                if demo_id and demo_id not in scope["project_ids"]:
                    failures.append(f"{username} 未绑定示例项目")
                if not any(kb["code"] == "KB-NATIONAL" for kb in kbs):
                    failures.append(f"{username} 看不到公共规范库 KB-NATIONAL")

    print()
    if failures:
        print(f"[FAIL] {len(failures)} 项未通过：")
        for line in failures:
            print(f"  {line}")
        return 1
    print("[OK] 真实账号的项目隔离与角色范围符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
