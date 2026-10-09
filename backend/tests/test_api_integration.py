# -*- coding: utf-8 -*-
"""端到端集成测试：HTTP 契约 + Agent 五阶段 + 引用校验（需要 PostgreSQL）。

无 PostgreSQL 时整体 skip；LLM 使用 Fake 网关，保证离线可复现。
"""
from __future__ import annotations

import uuid

import pytest

pytestmark = pytest.mark.integration

ADMIN = {"username": "admin", "password": "Admin@12345"}


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(scope="module")
def token(client) -> str:
    response = client.post("/api/v1/auth/login", json=ADMIN)
    if response.status_code != 200:
        pytest.skip("管理员账号不可用，请先执行 scripts/seed_data.py")
    return response.json()["data"]["access_token"]


@pytest.fixture(scope="module")
def auth_headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def test_health_endpoint_reports_components(client):
    body = client.get("/api/v1/health").json()
    assert body["code"] == 0
    components = body["data"]["components"]
    for key in ("database", "neo4j", "embedding", "reranker", "llm"):
        assert key in components, f"健康检查缺少组件：{key}"
    assert components["database"]["ok"] is True


def test_login_rejects_wrong_password(client):
    body = client.post("/api/v1/auth/login", json={"username": "admin", "password": "wrong-password"}).json()
    assert body["code"] == 40101
    assert body["trace_id"], "错误响应必须带 trace_id 便于排障"


def test_protected_endpoint_requires_token(client):
    response = client.get("/api/v1/kb/documents")
    assert response.status_code == 401
    assert response.json()["code"] == 40101


def test_invalid_token_is_rejected(client):
    body = client.get(
        "/api/v1/kb/documents", headers={"Authorization": "Bearer not-a-real-token"}
    ).json()
    assert body["code"] in {40101, 40102}


def test_me_endpoint_returns_current_user(client, auth_headers):
    body = client.get("/api/v1/auth/me", headers=auth_headers).json()
    assert body["code"] == 0
    assert body["data"]["username"] == "admin"
    assert body["data"]["role"] == "admin"


def test_seed_documents_are_listed(client, auth_headers):
    body = client.get("/api/v1/kb/documents?page_size=50", headers=auth_headers).json()
    assert body["code"] == 0
    assert body["data"]["meta"]["total"] >= 1, "应先执行 scripts/seed_data.py 写入示例规范"


def test_search_retrieves_temperature_clause(client, auth_headers):
    """核心检索断言：入模温度问题必须命中 GB 50204-2015 的 5.3.3 或 5.3.3 等效条款。"""
    body = client.post(
        "/api/v1/retrieval/search",
        headers=auth_headers,
        json={"query": "混凝土浇筑入模温度不宜高于多少度", "top_k": 5},
    ).json()
    assert body["code"] == 0
    data = body["data"]
    assert data["no_evidence"] is False
    assert data["results"], "应至少召回一条条款"
    contents = " ".join(item["content"] or "" for item in data["results"])
    assert "入模温度" in contents
    top = data["results"][0]
    assert top["citation"]["chunk_id"], "必须提供溯源 chunk_id（FR-RET-09）"
    assert data["sub_queries"], "应输出 Multi-Query 子查询（FR-RET-03）"
    assert data["sub_queries"][0] == "混凝土浇筑入模温度不宜高于多少度", "原问题必须保留在子查询首位"


def test_search_reports_no_evidence_for_irrelevant_query(client, auth_headers):
    """FR-RET-10：无依据时必须明确返回 no_evidence，而不是硬凑结论。"""
    body = client.post(
        "/api/v1/retrieval/search",
        headers=auth_headers,
        json={"query": "量子计算机超导芯片的制冷剂选型要求", "top_k": 3},
    ).json()
    assert body["code"] == 0
    data = body["data"]
    if not data["no_evidence"]:
        # 若召回，分数必须很低（否则说明阈值失效）
        top_score = data["results"][0]["relevance_score"]
        assert top_score < 0.9, f"无关查询不应得到高分：{top_score}"


def test_chat_returns_citations(client, auth_headers):
    body = client.post(
        "/api/v1/retrieval/chat",
        headers=auth_headers,
        json={"query": "混凝土入模温度有什么要求"},
    ).json()
    assert body["code"] == 0
    data = body["data"]
    assert data["answer"]
    assert data["citations"], "有依据时必须返回引用列表"
    for citation in data["citations"]:
        assert citation["location"]
        assert citation["index"] >= 1


def test_eval_task_full_flow(client, auth_headers):
    """创建任务 → 执行 Agent → 查询详情 → 获取报告。"""
    created = client.post(
        "/api/v1/eval/tasks",
        headers=auth_headers,
        json={
            "eval_type": "inspection_lot",
            "specialty": "结构工程",
            "title": "pytest 集成用例",
            "object": {
                "part": "地下室剪力墙",
                "project_name": "集成测试项目",
                "records": [
                    {"name": "混凝土浇筑记录", "content": "入模温度 32℃，坍落度 180mm，连续浇筑 6 小时。"},
                    {"name": "养护记录", "content": "浇筑后 24h 浇水养护，累计 5 天。"},
                ],
            },
        },
    ).json()
    assert created["code"] == 0
    task_id = created["data"]["task_id"]

    run = client.post(f"/api/v1/eval/tasks/{task_id}/run", headers=auth_headers)
    assert run.status_code == 200
    run_data = run.json()["data"]
    assert run_data["current_state"] in {"COMPLETED", "NEED_HUMAN", "DEGRADED"}
    assert run_data["subtasks"], "任务拆解不应为空"
    assert run_data["matches"], "条款匹配不应为空"
    assert run_data["iteration_count"] >= 1
    assert run_data["guard"]["limits"]["max_iterations"] >= 1
    assert run_data["checkpoint_backend"] in {"postgres", "memory"}
    # 每条匹配都必须带引用溯源
    for match in run_data["matches"]:
        assert match["verdict"]
        assert match.get("citation") is not None

    detail = client.get(f"/api/v1/eval/tasks/{task_id}", headers=auth_headers).json()["data"]
    assert detail["current_state"] == run_data["current_state"]
    assert len(detail["steps"]) == 5, "五个阶段都应有轨迹记录"
    assert detail["state_snapshot"]["guard"]["limits"]["no_progress_limit"] >= 1
    assert detail["progress"] >= 0.9

    report = client.get(f"/api/v1/eval/tasks/{task_id}/report", headers=auth_headers).json()
    assert report["code"] == 0
    markdown = report["data"]["markdown"]
    assert "评估概述" in markdown and "总体结论" in markdown
    assert "辅助生成" in markdown


def test_eval_task_unknown_id_returns_404(client, auth_headers):
    body = client.get(f"/api/v1/eval/tasks/{uuid.uuid4()}", headers=auth_headers).json()
    assert body["code"] == 40401


def test_judge_score_endpoint(client, auth_headers):
    """对有报告的任务触发 Judge，校验评分维度与引用校验字段。"""
    listing = client.get("/api/v1/eval/tasks?page_size=20", headers=auth_headers).json()["data"]["items"]
    report_id = None
    for task in listing:
        detail = client.get(f"/api/v1/eval/tasks/{task['id']}", headers=auth_headers).json()["data"]
        if detail.get("report_id"):
            report_id = detail["report_id"]
            break
    if report_id is None:
        pytest.skip("没有可评审的报告，先运行评估任务")

    body = client.post(
        f"/api/v1/judge/reports/{report_id}/score",
        headers=auth_headers,
        json={"cross_model": False},
    ).json()
    assert body["code"] == 0
    data = body["data"]
    assert 0.0 <= data["total_score"] <= 5.0
    assert len(data["dimension_scores"]) == 5, "必须返回五个维度评分"
    assert "citation_check" in data
    assert data["citation_check"]["total"] >= 0
    assert isinstance(data["needs_human"], bool)


def test_judge_dashboard_aggregates(client, auth_headers):
    body = client.get("/api/v1/judge/dashboard", headers=auth_headers).json()
    assert body["code"] == 0
    data = body["data"]
    assert "grade_distribution" in data
    assert "dimension_averages" in data
    assert data["review_count"] >= 0


def test_report_content_carries_matches_for_citation_check(client, auth_headers, session):
    """回归：报告必须携带条款比对结果，否则 Judge 的引用校验拿不到任何引用。

    选取条件说明：必须挑「带真实引用（clause_no 非空）」的报告。
    Agent 在未检索到依据时会产出 ``insufficient_evidence`` 的降级比对
    （``citation={}``、``clause_no=null``），这类报告**本来就没有引用可校验**，
    引用提取返回 0 条是正确行为。若选取条件只判断 matches 非空，
    就会随机挑中降级报告而误报失败（历史上确实如此）。
    """
    listing = client.get("/api/v1/eval/tasks?page_size=20", headers=auth_headers).json()["data"]["items"]

    def has_real_citation(match: dict) -> bool:
        return bool(match.get("clause_no") or (match.get("citation") or {}).get("clause_no"))

    report_payload = None
    for task in listing:
        detail = client.get(f"/api/v1/eval/tasks/{task['id']}", headers=auth_headers).json()["data"]
        if not detail.get("report_id"):
            continue
        candidate = client.get(f"/api/v1/eval/tasks/{task['id']}/report", headers=auth_headers).json()["data"]
        matches = (candidate.get("content") or {}).get("matches") or []
        if any(has_real_citation(m) for m in matches if isinstance(m, dict)):
            report_payload = candidate
            break
    if report_payload is None:
        pytest.skip("没有带真实引用的报告，先执行一次评估任务")

    matches = report_payload["content"]["matches"]
    assert matches, "报告 content.matches 不应为空"
    for item in matches:
        assert item.get("verdict")
        assert item.get("citation") is not None, "每条比对都应带引用溯源"
    assert report_payload["content"].get("evidence_basis") is not None

    # 引用提取逻辑：必须能拿到全部真实引用（而不是 0 条）
    from app.api.routes.judge import _citations_of
    from app.db.models import EvalReport

    row = session.get(EvalReport, uuid.UUID(report_payload["id"]))
    assert row is not None
    citations, conclusions = _citations_of(row)
    assert citations, "引用校验输入不应为空，否则幻觉识别形同失效"
    assert all(item["clause_no"] for item in citations)


def test_citation_extraction_skips_degraded_matches(session):
    """降级比对（insufficient_evidence）不应产生虚假引用。

    这是与上一条互补的用例：无依据的比对**必须**被引用提取跳过，
    否则会把「没有依据」包装成「有依据」，反而制造幻觉。
    """
    from app.api.routes.judge import _citations_of

    class _Row:
        def __init__(self, content):
            self.content = content
            self.id = "00000000-0000-0000-0000-000000000000"

    degraded = {
        "matches": [
            {
                "verdict": "insufficient_evidence",
                "citation": {},
                "clause_no": None,
                "spec_code": None,
                "reasoning": "未检索到可支撑判定的规范条款",
            }
        ],
        "evidence_basis": [],
        "analysis": {},
    }
    citations, _ = _citations_of(_Row(degraded))
    assert citations == [], "降级比对不应产生引用"


def test_audit_log_records_actions(client, auth_headers):
    body = client.get("/api/v1/admin/audit-logs?page_size=10", headers=auth_headers).json()
    assert body["code"] == 0
    items = body["data"]["items"]
    assert items, "登录等操作应写入审计日志"
    assert any(item["action"] in {"login", "retrieval", "chat", "eval_create"} for item in items)


def test_validation_error_returns_unified_envelope(client, auth_headers):
    body = client.post("/api/v1/retrieval/search", headers=auth_headers, json={}).json()
    assert body["code"] == 40001
    assert body["trace_id"]
