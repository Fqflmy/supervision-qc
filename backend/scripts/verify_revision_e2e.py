# -*- coding: utf-8 -*-
"""按修改建议逐项做端到端验收（在容器内对真实服务执行）。

逐项对应：
  P0-2.1  工程师不传 project_id 也能创建任务（单项目自动归属）
  P0-2.1b 越权项目仍被拒绝（隔离未被放松）
  P0-2.2  只读用户不能提交复核反馈
  新增    只读用户不能触发任务执行（实施中发现的越权）
  P1-3.1  只读用户能查规范（kb:read 可用）
  P1-3.3  kb_manager 账号可用且能维护知识库
  P2-3.4  权限点边界
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
PASSWORD = "Admin@12345"

passed: list[str] = []
failed: list[str] = []


def call(method: str, path: str, token: str | None = None, payload: dict | None = None):
    url = f"{BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except Exception:  # noqa: BLE001
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return exc.code, raw
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def login(username: str) -> str | None:
    code, body = call("POST", "/api/v1/auth/login",
                      payload={"username": username, "password": PASSWORD})
    if code != 200:
        print(f"    [X] {username} 登录失败 {code} {str(body)[:150]}")
        return None
    return body["data"]["access_token"]


def check(label: str, condition: bool, detail: str = "") -> None:
    if condition:
        passed.append(label)
        print(f"  [OK] {label}{('  ' + detail) if detail else ''}")
    else:
        failed.append(f"{label}  {detail}")
        print(f"  [X ] {label}  {detail}")


def main() -> int:
    print(f"=== 端到端验收：{BASE} ===")
    print()

    tokens = {}
    print("=== 准备各角色登录 ===")
    for role in ["admin", "engineer", "expert", "kb_manager", "viewer"]:
        tokens[role] = login(role)
    if not all(tokens.values()):
        print("  [X] 有角色登录失败，终止")
        return 1
    print("  5 个角色全部登录成功")
    print()

    # ---------- P0-2.1 工程师创建任务 ----------
    print("=== P0-2.1 工程师创建任务（不传 project_id）===")
    eng = tokens["engineer"]

    code, projects = call("GET", "/api/v1/me/projects", eng)
    check("工程师可查询自己被授权的项目", code == 200 and projects.get("data", {}).get("total", 0) >= 1,
          f"code={code} total={projects.get('data',{}).get('total') if code==200 else '-'}")

    code, created = call("POST", "/api/v1/eval/tasks", eng, {
        "eval_type": "inspection_lot",
        "specialty": "结构工程",
        "title": "端到端验收·单项目自动归属",
        "object": {"part": "现浇混凝土结构", "description": "验证修改建议 P0-2.1"},
    })
    ok_create = code in (200, 201)
    check("工程师**不传 project_id** 也能创建任务", ok_create,
          f"code={code} {str(created)[:180] if not ok_create else ''}")
    task_id = created["data"]["task_id"] if ok_create else None

    if task_id:
        code, detail = call("GET", f"/api/v1/eval/tasks/{task_id}", eng)
        proj = detail.get("data", {}).get("project_id") if code == 200 else None
        allowed_ids = [p["id"] for p in projects["data"]["items"]]
        check("任务自动归属到被授权项目", proj in allowed_ids,
              f"project_id={proj} 授权={allowed_ids}")

    # ---------- P0-2.1b 越权项目仍被拒 ----------
    print()
    print("=== P0-2.1b 隔离未被放松（显式传未授权项目）===")
    code, allp = call("GET", "/api/v1/admin/projects", tokens["admin"])
    if code == 200:
        allowed_ids = [p["id"] for p in projects["data"]["items"]]
        others = [p["id"] for p in allp["data"] if p["id"] not in allowed_ids]
        if others:
            code2, _ = call("POST", "/api/v1/eval/tasks", eng, {
                "project_id": others[0],
                "eval_type": "inspection_lot",
                "specialty": "结构工程",
                "title": "越权项目测试",
                "object": {"part": "越权", "description": "应被拒绝"},
            })
            check("在未授权项目下创建任务被拒绝", code2 == 403, f"code={code2}")
        else:
            check("在未授权项目下创建任务被拒绝", True, "（无其它项目可测，跳过）")
    else:
        check("在未授权项目下创建任务被拒绝", False, "无法取项目列表")

    # ---------- 写操作鉴权 ----------
    print()
    print("=== 写操作鉴权（只读用户不得触发/写入）===")
    vw = tokens["viewer"]
    if task_id:
        code, _ = call("POST", f"/api/v1/eval/tasks/{task_id}/run", vw)
        check("只读用户不能触发任务执行（会消耗 Token）", code == 403, f"code={code}")
        code, _ = call("POST", f"/api/v1/eval/tasks/{task_id}/submit", vw)
        check("只读用户不能异步提交任务", code == 403, f"code={code}")

    code, _ = call("POST", "/api/v1/eval/feedback", vw, {
        "task_id": task_id,
        "action": "confirm",
        "dimension": "conclusion_reasonableness",
        "original_value": "1",
        "corrected_value": "0",
        "comment": "只读用户不应能提交",
    })
    check("只读用户不能提交复核反馈", code == 403, f"code={code}")

    code, _ = call("POST", "/api/v1/eval/feedback", tokens["expert"], {
        "task_id": task_id,
        "action": "confirm",
        "dimension": "conclusion_reasonableness",
        "original_value": "1",
        "corrected_value": "1",
        "comment": "审核人员确认（验收）",
    })
    check("审核人员可提交复核反馈", code not in (403,), f"code={code}")

    # ---------- P1-3.1 只读用户查规范 ----------
    print()
    print("=== P1-3.1 只读用户查规范 ===")
    code, kbs = call("GET", "/api/v1/kb", vw)
    check("只读用户可列出知识库", code == 200, f"code={code}")
    code, docs = call("GET", "/api/v1/kb/documents", vw)
    check("只读用户可列出规范文档", code == 200, f"code={code}")
    code, _ = call("POST", "/api/v1/kb/documents", vw, {})
    check("只读用户不能上传规范", code == 403, f"code={code}")

    # ---------- P1-3.3 kb_manager ----------
    print()
    print("=== P1-3.3 知识库管理员可用 ===")
    km = tokens["kb_manager"]
    code, _ = call("GET", "/api/v1/kb/documents", km)
    check("kb_manager 可查看文档", code == 200, f"code={code}")
    code, _ = call("GET", "/api/v1/kb/kg/stats", km)
    check("kb_manager 可查看图谱统计", code == 200, f"code={code}")
    # 上传需 multipart，这里用图谱抽取验证写权限（需 doc_id）
    if code == 200:
        code2, _ = call("POST", "/api/v1/kb/kg/extract", km, {"doc_id": 1})
        check("kb_manager 具备图谱构建写权限", code2 != 403, f"code={code2}")
    code, _ = call("GET", "/api/v1/admin/users", km)
    check("kb_manager 不能管理用户", code == 403, f"code={code}")

    # ---------- 运行总览仅管理员 ----------
    print()
    print("=== 运行总览归管理员（其余角色无权）===")
    for role in ["engineer", "expert", "kb_manager", "viewer"]:
        code, body = call("GET", "/api/v1/admin/config", tokens[role])
        check(f"{role} 不能读运行配置", code == 403, f"code={code}")

    print()
    print("=" * 62)
    print(f"通过 {len(passed)} 项，失败 {len(failed)} 项")

    # 清理本次验收产生的任务：验收数据不应留在业务库里
    # （此前会留下 PENDING 任务，重新盘点数据时容易被误认为真实业务数据）
    cleanup_acceptance_tasks()

    if failed:
        print()
        print("失败明细：")
        for item in failed:
            print(f"  - {item}")
        return 1
    print("[OK] 全部验收通过")
    return 0


#: 本脚本创建的任务标题前缀
ACCEPTANCE_TITLE_PREFIX = "端到端验收"


def cleanup_acceptance_tasks() -> None:
    """清理本脚本创建的验收任务及其关联数据。"""
    try:
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
        )
        from app.db.session import session_scope

        with session_scope() as session:
            rows = (
                session.execute(
                    select(EvalTask).where(EvalTask.title.like(f"{ACCEPTANCE_TITLE_PREFIX}%"))
                )
                .scalars()
                .all()
            )
            ids = [t.id for t in rows]
            if not ids:
                return
            reports = (
                session.execute(
                    select(EvalReport).where(EvalReport.task_id.in_(ids))
                )
                .scalars()
                .all()
            )
            report_ids = [r.id for r in reports]
            if report_ids:
                session.execute(delete(JudgeScore).where(JudgeScore.report_id.in_(report_ids)))
                session.execute(delete(JudgeReview).where(JudgeReview.report_id.in_(report_ids)))
                session.execute(delete(EvalReport).where(EvalReport.id.in_(report_ids)))
            for model in (
                HumanFeedback,
                EvalSubtask,
                MatchResult,
                AgentStepLog,
                AgentToolCall,
            ):
                session.execute(delete(model).where(model.task_id.in_(ids)))
            session.execute(delete(EvalTask).where(EvalTask.id.in_(ids)))
        print(f"  已清理验收任务 {len(ids)} 个")
    except Exception as exc:  # noqa: BLE001
        print(f"  [警告] 验收数据清理失败（可手工运行 reset_data.py）：{exc}")


if __name__ == "__main__":
    raise SystemExit(main())
