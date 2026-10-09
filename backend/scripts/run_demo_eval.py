# -*- coding: utf-8 -*-
"""跑一次示例评估并打印结果（供编写操作手册时核对预期输出）。

用法（容器内）：
    python scripts/run_demo_eval.py <task_id>
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8000"


def call(method: str, path: str, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=1200) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return exc.code, raw
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def main() -> int:
    task_id = sys.argv[1]
    code, body = call("POST", "/api/v1/auth/login",
                      body={"username": "engineer", "password": "Admin@12345"})
    if code != 200:
        print(f"  登录失败 {code}")
        return 1
    token = body["data"]["access_token"]
    print("  登录成功（engineer）")

    print("  开始执行评估（真实 LLM，请等待）…")
    started = time.time()
    code, body = call("POST", f"/api/v1/eval/tasks/{task_id}/run", token)
    elapsed = time.time() - started
    print(f"  执行完成：code={code} 耗时={elapsed:.0f}s")
    if code != 200:
        print(f"  错误：{str(body)[:300]}")
        return 1

    data = body["data"]
    print(f"  任务状态  : {data.get('current_state')}")
    print(f"  迭代次数  : {data.get('iteration_count')}")
    print(f"  Token 消耗: {data.get('total_tokens')}")
    print(f"  转人工复核: {data.get('needs_human')}")
    if data.get("guard_reason"):
        print(f"  守卫原因  : {str(data['guard_reason'])[:120]}")

    code, report = call("GET", f"/api/v1/eval/tasks/{task_id}/report", token)
    if code == 200:
        rep = report["data"]
        print()
        print("  === 报告 ===")
        print(f"  总体结论  : {rep.get('overall_verdict')}")
        print(f"  风险等级  : {rep.get('risk_level')}")
        print(f"  依据条款数: {rep.get('basis_count')}")
        print(f"  问题项数  : {rep.get('non_compliance_count')}")
        judge = rep.get("judge") or {}
        if judge:
            print(f"  Judge 总分: {judge.get('total_score')}  等级={judge.get('grade')}  需复核={judge.get('needs_human')}")
        review_info = rep.get("review") or {}
        print(f"  复核状态  : {review_info.get('review_status_label')}  已签发={review_info.get('is_final')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
