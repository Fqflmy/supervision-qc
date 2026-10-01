# -*- coding: utf-8 -*-
"""真实 HTTP 前后端联调检查。

后端以真实进程（uvicorn）运行，本脚本模拟前端页面实际调用的接口，
校验统一响应体、鉴权、以及各页面依赖的字段是否齐备。
"""
from __future__ import annotations

import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BASE = "http://127.0.0.1:8000/api/v1"
ADMIN = {"username": "admin", "password": "Admin@12345"}


def call(method: str, path: str, token: str | None = None, payload: dict | None = None) -> tuple[int, dict]:
    url = f"{BASE}{path}"
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return response.status, json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8")
        try:
            return exc.code, json.loads(body)
        except json.JSONDecodeError:
            return exc.code, {"raw": body[:200]}


def wait_ready(timeout: float = 60.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            status, body = call("GET", "/health")
            if status == 200 and body.get("code") == 0:
                return True
        except Exception:  # noqa: BLE001
            pass
        time.sleep(1.5)
    return False


def main() -> int:
    if not wait_ready():
        print("[FAIL] 后端未就绪，请先启动 uvicorn app.main:app")
        return 2

    checks: list[tuple[str, bool, str]] = []

    def check(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))
        print(f"  [{'OK ' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")

    print("== 前端页面依赖接口 ==")

    status, health = call("GET", "/health")
    components = health["data"]["components"]
    check("登录页 · 健康检查", status == 200 and health["code"] == 0,
          f"db={components['database']['ok']} neo4j={components['neo4j']['ok']} "
          f"embedder={components['embedding']['name']} reranker={components['reranker']['name']}")

    status, login = call("POST", "/auth/login", payload=ADMIN)
    ok = status == 200 and login["code"] == 0
    check("登录页 · 登录", ok, login.get("message", ""))
    if not ok:
        return 1
    token = login["data"]["access_token"]
    check("登录页 · 响应字段", all(k in login["data"] for k in ("access_token", "refresh_token", "user")),
          f"user={login['data']['user']['username']} role={login['data']['user']['role']}")

    status, me = call("GET", "/auth/me", token)
    check("主框架 · 用户信息", me["code"] == 0 and me["data"]["role"] == "admin")

    status, kbs = call("GET", "/kb", token)
    check("知识库页 · 空间列表", kbs["code"] == 0, f"{len(kbs['data'])} 个知识库")

    status, docs = call("GET", "/kb/documents?page=1&page_size=20", token)
    items = docs["data"]["items"]
    check("知识库页 · 文档列表", docs["code"] == 0 and "meta" in docs["data"],
          f"total={docs['data']['meta']['total']}")
    doc_id = items[0]["id"] if items else None

    if doc_id:
        status, chunks = call("GET", f"/kb/documents/{doc_id}/chunks?page=1&page_size=5", token)
        check("规范详情页 · 分块列表", chunks["code"] == 0,
              f"chunks={chunks['data']['meta']['total']}")
        status, detail = call("GET", f"/kb/documents/{doc_id}", token)
        check("规范详情页 · 文档详情", detail["code"] == 0 and "versions" in detail["data"],
              f"versions={len(detail['data']['versions'])}")

    status, stats = call("GET", "/kb/kg/stats", token)
    check("图谱页 · 图谱统计", stats["code"] == 0, json.dumps(stats["data"], ensure_ascii=False))

    status, chain = call("GET", "/kb/kg/clauses/5.3.3/refs?direction=both&depth=2", token)
    check("图谱页 · 引用链追踪", chain["code"] == 0,
          f"edges={len(chain['data'].get('edges', []))} available={chain['data'].get('available')}")

    status, search = call("POST", "/retrieval/search", token,
                          {"query": "混凝土浇筑入模温度不宜高于多少度", "top_k": 3})
    data = search["data"]
    check("问答页 · 混合检索", search["code"] == 0 and not data["no_evidence"],
          f"top1={data['results'][0]['spec_code']} {data['results'][0]['clause_no']} "
          f"score={data['results'][0]['relevance_score']:.3f} latency={data['latency_ms']}ms")
    check("问答页 · 检索链路指标", bool(data.get("sub_queries")) and "recall" in data.get("debug", {}),
          f"sub_queries={len(data['sub_queries'])}")

    status, chat = call("POST", "/retrieval/chat", token, {"query": "混凝土入模温度有什么要求"})
    check("问答页 · 智能问答", chat["code"] == 0 and bool(chat["data"]["citations"]),
          f"citations={len(chat['data']['citations'])} 首条={chat['data']['citations'][0]['location'][:40]}")

    status, listing = call("GET", "/eval/tasks?page=1&page_size=5", token)
    check("评估任务页 · 任务列表", listing["code"] == 0, f"total={listing['data']['meta']['total']}")

    status, created = call("POST", "/eval/tasks", token, {
        "eval_type": "inspection_lot",
        "specialty": "结构工程",
        "title": "前后端联调用例",
        "object": {
            "part": "地下室剪力墙",
            "project_name": "联调项目",
            "records": [{"name": "混凝土浇筑记录", "content": "入模温度 32℃，养护 5 天。"}],
        },
    })
    check("评估任务页 · 新建任务", created["code"] == 0, created["data"]["task_id"])
    task_id = created["data"]["task_id"]

    status, run = call("POST", f"/eval/tasks/{task_id}/run", token)
    run_data = run["data"]
    check("任务详情页 · Agent 执行", run["code"] == 0 and run_data["current_state"] in
          {"COMPLETED", "NEED_HUMAN", "DEGRADED"},
          f"state={run_data['current_state']} iterations={run_data['iteration_count']} "
          f"matches={len(run_data['matches'])} checkpoint={run_data['checkpoint_backend']}")

    status, tdetail = call("GET", f"/eval/tasks/{task_id}", token)
    steps = tdetail["data"]["steps"]
    check("任务详情页 · 轨迹与状态机", len(steps) == 5 and tdetail["data"]["state_snapshot"] is not None,
          "steps=" + ",".join(f"{s['step']}:{s['status']}" for s in steps))

    status, report = call("GET", f"/eval/tasks/{task_id}/report", token)
    markdown = report["data"].get("markdown") or ""
    check("任务详情页 · 报告渲染", report["code"] == 0 and "总体结论" in markdown,
          f"markdown={len(markdown)}字 basis={report['data']['basis_count']}")
    report_id = report["data"]["id"]

    status, judged = call("POST", f"/judge/reports/{report_id}/score", token, {"cross_model": False})
    check("任务详情页 · 触发评审", judged["code"] == 0,
          f"total={judged['data']['total_score']} dimensions={len(judged['data']['dimension_scores'])} "
          f"hallucinations={judged['data']['citation_check']['hallucinations']}")

    status, dashboard = call("GET", "/judge/dashboard", token)
    check("质量评审页 · 看板", dashboard["code"] == 0,
          f"reviews={dashboard['data']['review_count']} avg={dashboard['data']['avg_total_score']}")

    status, metrics = call("GET", "/metrics", token)
    check("总览页 · 运行指标", metrics["code"] == 0,
          f"docs={metrics['data']['documents_total']} chunks={metrics['data']['chunks_total']} "
          f"tokens={metrics['data']['tokens_total']}")

    status, config = call("GET", "/admin/config", token)
    check("系统页 · 运行配置", config["code"] == 0, ",".join(config["data"].keys()))

    status, logs = call("GET", "/admin/audit-logs?page=1&page_size=5", token)
    check("系统页 · 审计日志", logs["code"] == 0 and bool(logs["data"]["items"]),
          f"total={logs['data']['meta']['total']}")

    status, unauth = call("GET", "/kb/documents")
    check("鉴权边界 · 未带 Token", status == 401 and unauth["code"] == 40101)

    failed = [name for name, ok, _ in checks if not ok]
    print(f"\n合计 {len(checks)} 项检查，通过 {len(checks) - len(failed)} 项")
    if failed:
        print("失败项：" + "；".join(failed))
        return 1
    print("[OK] 前后端接口联调全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
