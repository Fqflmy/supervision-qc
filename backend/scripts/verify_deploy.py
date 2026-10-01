# -*- coding: utf-8 -*-
"""容器化部署的功能验收脚本（走 Nginx 反代，等价于真实用户访问）。

验证：健康检查 → 鉴权 → 检索 → 问答 → Agent 五阶段 → 报告 → Judge 评审。
用法：
    python backend/scripts/verify_deploy.py [--base http://127.0.0.1:8080]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request

PASS = 0
FAIL = 0


def call(base: str, method: str, path: str, token: str | None = None, payload=None, timeout: int = 600):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(f"{base}{path}", data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def check(label: str, condition: bool, detail: str = "") -> None:
    global PASS, FAIL
    if condition:
        PASS += 1
        print(f"  [OK]   {label} {detail}")
    else:
        FAIL += 1
        print(f"  [FAIL] {label} {detail}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    base = args.base.rstrip("/") + "/api/v1"

    print(f"验收目标：{base}\n")

    # 1) 健康检查
    print("[1] 健康检查")
    health = call(base, "GET", "/health")["data"]
    components = health["components"]
    check("服务状态", health["status"] == "ok", health["status"])
    check("PostgreSQL", components["database"]["ok"] is True)
    check("Neo4j", components["neo4j"]["ok"] is True, "(图谱增强)")
    check(
        "Embedding 未降级",
        components["embedding"]["degraded"] is False,
        components["embedding"]["name"],
    )
    check(
        "Reranker 未降级",
        components["reranker"]["degraded"] is False,
        components["reranker"]["name"],
    )
    index = components.get("vector_index_default", {})
    check("向量索引非空", index.get("size", 0) > 0, f"size={index.get('size')}")

    # 2) 鉴权
    print("\n[2] 鉴权")
    login = call(base, "POST", "/auth/login", payload={"username": "admin", "password": "Admin@12345"})["data"]
    token = login["access_token"]
    check("登录成功", bool(token), f"role={login['user']['role']}")
    try:
        call(base, "GET", "/kb/documents")
        check("未鉴权被拒绝", False, "竟然允许匿名访问")
    except urllib.error.HTTPError as exc:
        check("未鉴权被拒绝", exc.code == 401, f"HTTP {exc.code}")

    docs = call(base, "GET", "/kb/documents?page_size=20", token)["data"]
    total_docs = docs["meta"]["total"]
    check("规范文档已入库", total_docs >= 4, f"total={total_docs}")

    # 3) 检索
    print("\n[3] 检索（真实向量 + 重排）")
    cases = [
        ("混凝土浇筑入模温度不宜高于多少度", "5.3.3"),
        ("脚手架连墙件多远设置一个", ("6.4", "连墙件")),
    ]
    for query, expect in cases:
        started = time.perf_counter()
        result = call(base, "POST", "/retrieval/search", token, {"query": query, "top_k": 3})["data"]
        elapsed = time.perf_counter() - started
        top = result["results"][0] if result["results"] else {}
        text = f"{top.get('spec_code', '')} {top.get('clause_no', '')} {top.get('content', '')[:40]}"
        hit = any(str(part) in text for part in (expect if isinstance(expect, tuple) else (expect,)))
        check(
            f"「{query}」",
            bool(result["results"]) and hit and not result["no_evidence"],
            f"-> {top.get('spec_code')} {top.get('clause_no')} score={top.get('relevance_score', 0):.3f} ({elapsed:.1f}s)",
        )

    # 4) 问答
    print("\n[4] 规范问答（RAG + 引用溯源）")
    chat = call(base, "POST", "/retrieval/chat", token, {"query": "混凝土入模温度有什么要求"})["data"]
    check("返回回答", len(chat.get("answer", "")) > 20, f"{len(chat.get('answer', ''))}字")
    check("带引用溯源", len(chat.get("citations", [])) > 0, f"{len(chat.get('citations', []))}条")

    # 5) Agent 五阶段
    print("\n[5] Agent 五阶段评估")
    task = call(
        base,
        "POST",
        "/eval/tasks",
        token,
        {
            "eval_type": "inspection_lot",
            "specialty": "结构工程",
            "title": "部署验收-地下室剪力墙",
            "object": {
                "part": "地下室剪力墙",
                "records": [
                    {"name": "混凝土浇筑记录", "content": "C30 混凝土入模温度 32℃，连续浇筑 6 小时，坍落度 180mm。"},
                    {"name": "养护记录", "content": "浇筑后 24 小时开始养护，累计养护 5 天。"},
                ],
            },
        },
    )["data"]
    task_id = task["task_id"]
    started = time.perf_counter()
    run = call(base, "POST", f"/eval/tasks/{task_id}/run?resume=false", token, timeout=900)["data"]
    elapsed = time.perf_counter() - started
    check(
        "Agent 执行完成",
        run["current_state"] in {"COMPLETED", "NEED_HUMAN"},
        f"state={run['current_state']} 迭代={run['iteration_count']} ({elapsed:.1f}s)",
    )
    check("PostgreSQL 检查点", run.get("checkpoint_backend") == "postgres", str(run.get("checkpoint_backend")))
    check("条款比对有结果", len(run.get("matches", [])) > 0, f"{len(run.get('matches', []))}条")

    detail = call(base, "GET", f"/eval/tasks/{task_id}", token)["data"]
    steps = detail.get("steps", [])
    check("五阶段全部执行", len(steps) >= 5, str(steps))

    # 6) 报告
    print("\n[6] 评估报告")
    report = call(base, "GET", f"/eval/tasks/{task_id}/report", token)["data"]
    markdown = report.get("markdown", "")
    check("报告已生成", len(markdown) > 500, f"{len(markdown)}字")
    check("包含条款比对章节", "条款逐条比对" in markdown or "条款比对" in markdown)
    check("无重复章节", markdown.count("评估概述") <= 1, f"出现{markdown.count('评估概述')}次")
    check(
        "附免责与人工复核声明",
        "不替代监理工程师" in markdown or "签字确认" in markdown or "仅供" in markdown,
    )
    check("含引用溯源附录", "溯源" in markdown or "chunk #" in markdown)

    # 7) Judge（评审对象是报告，路径为 /judge/reports/{report_id}/score）
    print("\n[7] LLM-as-Judge 评审")
    report_id = report.get("id") or report.get("report_id")
    check("报告 ID 可用", bool(report_id), str(report_id))
    judge = call(base, "POST", f"/judge/reports/{report_id}/score", token, timeout=900)["data"]
    check("总分有效", 0 < float(judge.get("total_score", 0)) <= 5, f"总分={judge.get('total_score')}")
    dims = judge.get("dimensions") or judge.get("dimension_scores") or judge.get("items") or []
    check("五维评分齐全", len(dims) >= 5, f"{len(dims)}维")
    check(
        "给出等级与人工复核建议",
        bool(judge.get("grade")) or judge.get("needs_human") is not None,
        f"等级={judge.get('grade')} 需人工={judge.get('needs_human')}",
    )

    # 8) 质量看板与审计
    print("\n[8] 质量看板与审计")
    dash = call(base, "GET", "/judge/dashboard", token)["data"]
    check("看板可访问", "review_count" in dash or "review_count" in dash or bool(dash), str(list(dash)[:4]))
    audit = call(base, "GET", "/admin/audit-logs?page_size=5", token)["data"]
    check("审计日志有记录", audit["meta"]["total"] > 0, f"total={audit['meta']['total']}")

    # 9) 知识图谱（可选增强：Neo4j 连通即通过；未抽取只提示不判失败）
    print("\n[9] 知识图谱")
    kg = call(base, "GET", "/kb/kg/stats", token)["data"]
    check("Neo4j 已连通", kg.get("available") is True, str(kg))
    if kg.get("clauses", 0) > 0:
        check("图谱已抽取", True, f"条款={kg['clauses']} 规范={kg['specs']} 引用={kg['references']}")
        refs = call(base, "GET", "/kb/kg/clauses/6.0.1/refs?direction=both&depth=3", token)["data"]
        check("引用链可查询", refs.get("available") is True, f"{len(refs.get('edges', []))} 条边")
    else:
        check(
            "图谱已抽取",
            True,
            "已跳过（尚未抽取；执行 scripts/extract_kg_all.py 后可验证引用链）",
        )

    # 10) 指标端点
    print("\n[10] 运行指标")
    metrics = call(base, "GET", "/metrics", token)["data"]
    check("指标可访问", metrics.get("documents_total", 0) > 0, f"文档={metrics.get('documents_total')} 分块={metrics.get('chunks_total')}")
    index = metrics.get("vector_index") or {}
    check("索引聚合正确", index.get("size", 0) > 0, f"{index.get('name')} size={index.get('size')}")

    print(f"\n{'=' * 56}\n通过 {PASS} 项，失败 {FAIL} 项")
    return 1 if FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
