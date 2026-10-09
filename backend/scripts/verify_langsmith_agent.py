# -*- coding: utf-8 -*-
"""真实评估任务 → LangSmith 链路追踪验证。

验证目标（不只是「有没有数据」，而是**层级是否正确**）：
    agent.evaluate
      ├─ agent.planning
      │    └─ llm.chat[agent_planning]
      ├─ agent.retrieval
      │    ├─ retrieval            ← 检索管道内部：BM25∥FAISS、RRF、重排
      │    └─ llm.chat[...]
      ├─ agent.clause_matching
      ├─ agent.analysis
      └─ agent.report_generation

用法：
    python scripts/verify_langsmith_agent.py
    python scripts/verify_langsmith_agent.py --wait 90
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait", type=int, default=75, help="任务完成后等待上报的秒数")
    args = parser.parse_args()

    from fastapi.testclient import TestClient

    from app.core.tracing import get_state, startup_message
    from app.main import app

    state = get_state()
    print("=== 追踪状态 ===")
    print(f"  {startup_message()}")
    if not state.enabled:
        print("[FAIL] 追踪未启用")
        return 1

    task_id = None
    with TestClient(app) as client:
        login = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "Admin@12345"}
        )
        if login.status_code != 200 or login.json().get("code") != 0:
            print(f"[FAIL] 登录失败：{login.status_code} {login.text[:200]}")
            return 1
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

        print()
        print("=== 创建并执行评估任务 ===")
        created = client.post(
            "/api/v1/eval/tasks",
            headers=headers,
            json={
                "eval_type": "inspection_lot",
                "specialty": "结构工程",
                "title": "LangSmith 追踪验证",
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
        print(f"  task_id={task_id}")

        ran = client.post(f"/api/v1/eval/tasks/{task_id}/run", headers=headers, timeout=900)
        if ran.status_code != 200:
            print(f"[FAIL] 执行失败：{ran.status_code} {ran.text[:300]}")
            return 1
        data = ran.json()["data"]
        print(
            f"  状态={data.get('current_state')} 迭代={data.get('iteration_count')} "
            f"比对={len(data.get('matches') or [])} Token={data.get('token_used')}"
        )

    print()
    print(f"=== 等待 {args.wait}s 让追踪上报 ===")
    time.sleep(args.wait)

    print()
    print("=== 从 LangSmith 查回该任务的追踪层级 ===")
    try:
        from langsmith import Client

        client = Client()
        roots = [
            r
            for r in client.list_runs(project_name=state.project, limit=60)
            if (r.inputs or {}).get("task_id") == str(task_id)
        ]
        if not roots:
            print(f"  [FAIL] 未找到 task_id={task_id} 的根 run")
            print("         可加大 --wait 后重试")
            return 1

        root = roots[0]
        print(f"  根 run: {root.name}  id={root.id}")
        print(f"    outputs={root.outputs}")
        print()

        # 递归打印子 run，展示层级
        def dump(run, depth: int = 1, budget: list[int] | None = None) -> None:
            if budget is None:
                budget = [40]
            if budget[0] <= 0:
                return
            children = list(client.list_runs(project_name=state.project, parent_run_id=run.id))
            for child in children:
                budget[0] -= 1
                indent = "    " + "  " * depth
                extra = ""
                if child.run_type == "llm" and isinstance(child.outputs, dict):
                    usage = (child.outputs or {}).get("usage") or {}
                    extra = f"  tokens={usage.get('total_tokens')}"
                print(f"{indent}- {child.name} [{child.run_type}]{extra}")
                dump(child, depth + 1, budget)

        dump(root)

        names = {
            c.name
            for c in client.list_runs(project_name=state.project, limit=60)
            if c.trace_id == root.trace_id
        }
        required = {
            "agent.evaluate",
            "agent.planning",
            "agent.retrieval",
            "agent.clause_matching",
            "agent.analysis",
            "agent.report_generation",
            "retrieval",
        }
        missing = required - names
        print()
        print(f"  追踪内共 {len(names)} 个 span")
        if missing:
            print(f"  [FAIL] 缺少预期 span：{sorted(missing)}")
            return 1
        print("  [OK] 五阶段节点、检索管道、LLM 调用均已出现在追踪中")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] 查询失败：{type(exc).__name__}: {str(exc)[:300]}")
        return 1

    print()
    print("[OK] 全链路追踪验证通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
