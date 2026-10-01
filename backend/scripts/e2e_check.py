# -*- coding: utf-8 -*-
"""端到端验证：登录 → 检索 → 问答 → 评估任务（Agent 五阶段）→ 引用校验。

默认离线运行（Fake 模型），用于验证链路与契约完整性；
加 --live 使用 .env 中配置的真实 DeepSeek 模型。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

SEED_KB_ID = None


def setup_env(live: bool) -> None:
    if not live:
        os.environ["SUPERVISION_LLM_PROVIDER"] = "fake"
    os.environ.setdefault("SUPERVISION_LOG_LEVEL", "WARNING")
    os.environ.setdefault("SUPERVISION_LOG_JSON", "false")
    # Windows：psycopg 异步需要 SelectorEventLoop，必须在创建事件循环前设置
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


def pick_kb_id() -> int | None:
    from sqlalchemy import select

    from app.db import session_scope
    from app.db.models import KnowledgeBase

    with session_scope() as session:
        kb = session.execute(select(KnowledgeBase).order_by(KnowledgeBase.id)).scalars().first()
        return kb.id if kb else None


def run_http_flow(live: bool) -> dict:
    from fastapi.testclient import TestClient

    from app.main import app

    results: dict = {}
    with TestClient(app) as client:
        # 1) 健康检查
        health = client.get("/api/v1/health").json()
        assert health["code"] == 0, health
        comp = health["data"]["components"]
        print(f"[1] 健康检查 status={health['data']['status']} db={comp['database']['ok']} "
              f"neo4j={comp['neo4j']['ok']} embedder={comp['embedding']['name']} "
              f"reranker={comp['reranker']['name']} llm={comp['llm']['primary_model']}")
        results["health"] = health["data"]

        # 2) 登录
        login = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "Admin@12345"}
        )
        assert login.status_code == 200, login.text
        token = login.json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        print(f"[2] 登录成功 user={login.json()['data']['user']['username']} "
              f"role={login.json()['data']['user']['role']}")

        # 3) 未鉴权访问应被拒绝（FR-SYS-02）
        unauth = client.get("/api/v1/kb/documents")
        assert unauth.status_code == 401, unauth.text
        print(f"[3] 未鉴权访问被拒绝 code={unauth.json()['code']}")

        # 4) 文档列表
        docs = client.get("/api/v1/kb/documents", headers=headers).json()
        assert docs["code"] == 0
        print(f"[4] 文档列表 total={docs['data']['meta']['total']}")

        # 5) 检索（FR-RET）
        search = client.post(
            "/api/v1/retrieval/search",
            headers=headers,
            json={"query": "混凝土浇筑入模温度不宜高于多少度", "top_k": 3},
        ).json()
        assert search["code"] == 0, search
        data = search["data"]
        assert not data["no_evidence"], "应检索到入模温度条款"
        top = data["results"][0]
        print(f"[5] 检索 top1={top['spec_code']} {top['clause_no']} "
              f"score={top['relevance_score']:.3f} 溯源={top['chapter_path']} latency={data['latency_ms']}ms")
        assert "30" in top["content"] or "28" in top["content"], f"命中内容不符：{top['content'][:120]}"
        results["retrieval"] = data

        # 6) 问答（含引用标注）
        chat = client.post(
            "/api/v1/retrieval/chat",
            headers=headers,
            json={"query": "混凝土入模温度有什么要求"},
        ).json()
        assert chat["code"] == 0, chat
        print(f"[6] 问答 引用数={len(chat['data']['citations'])} "
              f"首条={chat['data']['citations'][0]['location'][:60] if chat['data']['citations'] else '-'}")
        results["chat"] = chat["data"]

        # 7) 创建评估任务（FR-AGT）
        task = client.post(
            "/api/v1/eval/tasks",
            headers=headers,
            json={
                "eval_type": "inspection_lot",
                "specialty": "结构工程",
                "title": "地下室剪力墙混凝土施工质量评估",
                "object": {
                    "part": "地下室剪力墙",
                    "project_name": "示范工程·某住宅小区 1# 楼",
                    "description": "夏季施工，入模温度偏高",
                    "records": [
                        {"name": "混凝土浇筑记录", "content": "C30 混凝土入模温度 32℃，连续浇筑 6 小时，坍落度 180mm。"},
                        {"name": "养护记录", "content": "浇筑完成后 24h 开始浇水养护，累计养护 5 天。"},
                        {"name": "原材料检验", "content": "水泥出厂合格证齐全，进场复验报告已提供，见证取样记录完整。"},
                    ],
                },
                "kb_ids": [SEED_KB_ID] if SEED_KB_ID else None,
            },
        ).json()
        assert task["code"] == 0, task
        task_id = task["data"]["task_id"]
        print(f"[7] 任务创建 task_id={task_id} state={task['data']['current_state']}")

        # 8) 执行 Agent 五阶段
        started = time.perf_counter()
        run = client.post(f"/api/v1/eval/tasks/{task_id}/run", headers=headers)
        assert run.status_code == 200, run.text
        body = run.json()
        assert body["code"] == 0, body
        run_data = body["data"]
        print(
            f"[8] Agent 执行完成 state={run_data['current_state']} "
            f"iterations={run_data['iteration_count']} 子任务={len(run_data['subtasks'])} "
            f"条款比对={len(run_data['matches'])} tokens={run_data['token_used']} "
            f"耗时={time.perf_counter() - started:.1f}s checkpoint={run_data['checkpoint_backend']}"
        )
        print(f"    守卫: {json.dumps(run_data['guard']['limits'], ensure_ascii=False)}")
        for idx, match in enumerate(run_data["matches"], start=1):
            print(f"    [{idx}] {match.get('clause_no')} verdict={match.get('verdict')} "
                  f"risk={match.get('risk_level')}")
        print(f"    降级标记={run_data['degraded']} 错误={len(run_data['errors'])}")
        results["run"] = run_data

        # 9) 任务详情（状态机快照 + 轨迹）
        detail = client.get(f"/api/v1/eval/tasks/{task_id}", headers=headers).json()["data"]
        print(
            f"[9] 任务详情 state={detail['current_state']} progress={detail['progress']} "
            f"steps={[s['step'] + ':' + s['status'] for s in detail['steps']]}"
        )
        results["task_detail"] = detail
        results["task_id"] = task_id

        # 10) 报告
        report = client.get(f"/api/v1/eval/tasks/{task_id}/report", headers=headers).json()
        assert report["code"] == 0, report
        report_data = report["data"]
        markdown = report_data["markdown"]
        print(
            f"[10] 报告 overall={report_data['overall_verdict']} risk={report_data['risk_level']} "
            f"依据数={report_data['basis_count']} 问题数={report_data['non_compliance_count']} "
            f"markdown={len(markdown)}字"
        )
        assert "评估概述" in markdown and "总体结论" in markdown, "报告缺少必需章节"
        assert "辅助生成" in markdown, "报告缺少 AI 生成声明（SRS 7.6 合规）"
        assert "|" in markdown, "报告缺少表格"
        results["report"] = report_data
        results["report_id"] = report_data["id"]
    return results


async def run_judge(report_id: str, live: bool) -> dict:
    """Judge 评审（直接调用服务层，便于拿到完整评审明细）。"""
    from sqlalchemy import select

    from app.db import session_scope
    from app.db.models import EvalReport
    from app.judge.scorer import evaluate_report, persist_outcome

    with session_scope() as session:
        report = session.get(EvalReport, uuid.UUID(report_id))
        assert report is not None, "报告不存在"
        content = report.content or {}
        matches = content.get("matches") if isinstance(content.get("matches"), list) else []
        citations = [
            {
                "clause_no": m.get("clause_no") or (m.get("citation") or {}).get("clause_no"),
                "spec_code": m.get("spec_code") or (m.get("citation") or {}).get("spec_code"),
                "chunk_id": (m.get("citation") or {}).get("chunk_id"),
            }
            for m in matches
        ]
        citations.append({"clause_no": "99.9.9", "spec_code": "GB 00000-0000"})  # 注入幻觉引用
        conclusions = [str(m.get("reasoning") or "")[:500] for m in matches] + ["幻觉引用测试"]

        outcome = await evaluate_report(
            session,
            report_markdown=report.markdown or "",
            citations=citations,
            conclusions=conclusions,
            cross_model=False,
        )
        review = persist_outcome(session, report, outcome)
        payload = outcome.to_dict()
        payload["review_id"] = review.id
        print(
            f"[11] Judge 总分={outcome.total_score} 等级={outcome.grade_label} "
            f"需人工={outcome.needs_human} 幻觉引用={outcome.citation_check['hallucinations']} "
            f"引用准确率={outcome.citation_check['accuracy']}"
        )
        for item in outcome.dimension_scores:
            print(f"    {item.label}: {item.score} (权重 {item.weight}) {item.comment[:50]}")
        assert outcome.citation_check["hallucinations"] >= 1, "应识别出注入的幻觉引用"
        return payload


def main() -> int:
    parser = argparse.ArgumentParser(description="端到端验证")
    parser.add_argument("--live", action="store_true", help="使用真实 DeepSeek 模型")
    args = parser.parse_args()
    setup_env(args.live)

    global SEED_KB_ID
    SEED_KB_ID = pick_kb_id()
    if SEED_KB_ID is None:
        print("[FAIL] 知识库为空，请先执行 python scripts/seed_data.py")
        return 2
    print(f"使用知识库 kb_id={SEED_KB_ID}；模型模式={'真实 ' + os.environ.get('SUPERVISION_LLM_PRIMARY_MODEL', 'deepseek') if args.live else 'Fake（离线）'}\n")

    results = run_http_flow(args.live)
    asyncio.run(run_judge(results["report_id"], args.live))

    # 12) 审计日志（FR-SYS-03）与质量看板（FR-JDG-07）
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as client:
        token = client.post(
            "/api/v1/auth/login", json={"username": "admin", "password": "Admin@12345"}
        ).json()["data"]["access_token"]
        headers = {"Authorization": f"Bearer {token}"}
        logs = client.get(
            "/api/v1/admin/audit-logs", headers=headers, params={"page_size": 5}
        ).json()["data"]
        print(f"[12] 审计日志 total={logs['meta']['total']}，最近动作="
              f"{[item['action'] for item in logs['items'][:5]]}")

        dashboard = client.get("/api/v1/judge/dashboard", headers=headers).json()["data"]
        print(f"[13] 质量看板 评审数={dashboard['review_count']} 平均分={dashboard['avg_total_score']} "
              f"需人工={dashboard['needs_human_count']} 幻觉引用累计={dashboard['hallucination_total']}")
        print(f"     维度均分={json.dumps(dashboard['dimension_averages'], ensure_ascii=False)}")

        metrics = client.get("/api/v1/metrics", headers=headers).json()["data"]
        print(f"[14] 指标 文档={metrics['documents_total']} 分块={metrics['chunks_total']} "
              f"任务分布={json.dumps(metrics['tasks_by_state'], ensure_ascii=False)} "
              f"tokens={metrics['tokens_total']}")

    print("\n[OK] 端到端验证通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
