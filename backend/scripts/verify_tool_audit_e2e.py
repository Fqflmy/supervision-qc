# -*- coding: utf-8 -*-
"""端到端验证：真实评估任务是否产生工具调用审计，且检索未被授权层误伤。

验证内容
--------
1. 以管理员身份创建并同步执行一个评估任务；
2. 检查 ``agent_tool_call`` 表是否有该任务的调用记录；
3. 确认检索**没有被授权层误伤**（工具调用被放行、报告带引用）。

这是回归防线：授权层写得过严会静默导致「检索全部被拒、报告无依据」，
单元测试看不出这类问题，必须跑真实任务。

用法：
    python scripts/verify_tool_audit_e2e.py
    python scripts/verify_tool_audit_e2e.py --skip-run   # 只用已有任务检查
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--skip-run", action="store_true", help="不执行任务，仅检查已有审计")
    args = parser.parse_args()

    from fastapi.testclient import TestClient

    from app.db.models import AgentToolCall, EvalTask
    from app.db.session import session_scope
    from app.main import app

    failures: list[str] = []
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "Admin@12345"}
        )
        if login.status_code != 200 or login.json().get("code") != 0:
            print(f"[FAIL] 登录失败：{login.status_code} {login.text[:200]}")
            return 1
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

        task_id = None
        if not args.skip_run:
            created = client.post(
                "/api/v1/eval/tasks",
                headers=headers,
                json={
                    "eval_type": "inspection_lot",
                    "specialty": "结构工程",
                    "title": "工具审计端到端验证",
                    "object": {
                        "part": "现浇混凝土结构",
                        "item": "混凝土强度",
                        "description": "C30 混凝土试块强度检验批验收",
                    },
                },
            )
            if created.status_code != 200:
                print(f"[FAIL] 创建任务失败：{created.status_code} {created.text[:300]}")
                return 1
            task_id = created.json()["data"]["task_id"]
            print(f"[1] 已创建任务 {task_id[:8]}")

            ran = client.post(f"/api/v1/eval/tasks/{task_id}/run", headers=headers, timeout=900)
            if ran.status_code != 200:
                print(f"[FAIL] 执行任务失败：{ran.status_code} {ran.text[:300]}")
                return 1
            data = ran.json()["data"]
            print(f"    状态={data.get('current_state')} 迭代={data.get('iteration_count')}")

            report = client.get(f"/api/v1/eval/tasks/{task_id}/report", headers=headers)
            if report.status_code == 200:
                content = report.json()["data"].get("content") or {}
                matches = content.get("matches") or []
                with_cite = [m for m in matches if m.get("clause_no") or (m.get("citation") or {}).get("clause_no")]
                print(f"[2] 报告比对 {len(matches)} 条，带真实引用 {len(with_cite)} 条")
                if not with_cite:
                    failures.append("报告没有任何带引用的比对（检索可能被授权层误伤）")
            else:
                failures.append(f"获取报告失败：{report.status_code}")

        # ---------- 审计检查 ----------
        with session_scope() as session:
            if task_id is None:
                rows = (
                    session.query(AgentToolCall)
                    .order_by(AgentToolCall.id.desc())
                    .limit(200)
                    .all()
                )
                print(f"[3] 检查最近 {len(rows)} 条工具调用审计")
            else:
                rows = (
                    session.query(AgentToolCall)
                    .filter(AgentToolCall.task_id == task_id)
                    .order_by(AgentToolCall.seq)
                    .all()
                )
                print(f"[3] 任务 {task_id[:8]} 的工具调用审计：{len(rows)} 条")

            if not rows:
                failures.append("没有任何工具调用审计记录（工具层可能未接入）")
            else:
                allowed = sum(1 for r in rows if r.allowed)
                denied = [r for r in rows if not r.allowed]
                print(f"    放行 {allowed} 条 / 拒绝 {len(denied)} 条")
                for r in rows[:5]:
                    flag = "OK  " if r.allowed else "DENY"
                    print(
                        f"    [{flag}] seq={r.seq} tool={r.tool} "
                        f"user={r.user_id} {r.duration_ms}ms {r.reason or ''}"
                    )
                if denied:
                    failures.append(f"有 {len(denied)} 条工具调用被拒绝")

    print()
    if failures:
        print(f"[FAIL] {len(failures)} 项未通过：")
        for line in failures:
            print(f"  {line}")
        return 1
    print("[OK] 工具调用已审计，且检索未被授权层误伤（报告带真实引用）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
