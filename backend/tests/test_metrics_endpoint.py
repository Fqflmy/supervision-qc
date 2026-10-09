# -*- coding: utf-8 -*-
"""可观测性端点测试：Prometheus 文本指标格式与抓取鉴权。

背景
----
原先 ``prometheus.yml`` 抓的是 ``/api/v1/health``，返回统一响应包络
``{code, message, data, trace_id}`` —— **Prometheus 不支持 JSON**，
即便改抓 ``/api/v1/metrics`` 也无法解析，所以业务指标实际一条都没采到。
另外原端点需要 JWT，而 Prometheus 不会带令牌，必然 401。

本文件锁住修复结果：
1. ``/metrics`` 输出符合 Prometheus 文本暴露格式；
2. 抓取鉴权按配置生效（令牌 / 匿名 / 默认拒绝）；
3. 前端用的 ``/api/v1/metrics``（JSON）保持可用，未被替换。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.api.routes.metrics import (
    CONTENT_TYPE,
    authorize_scrape,
    render_metric,
    _escape_label,
    _fmt,
)


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# 文本格式渲染（纯函数，无需数据库）
# --------------------------------------------------------------------------- #
def test_render_metric_includes_help_and_type():
    lines = render_metric("supervision_documents_total", 12, help_text="规范文档总数")
    assert lines[0] == "# HELP supervision_documents_total 规范文档总数"
    assert lines[1] == "# TYPE supervision_documents_total gauge"
    assert lines[2] == "supervision_documents_total 12"


def test_render_metric_with_labels_sorted():
    """标签必须排序输出 —— 否则同一指标的顺序抖动会让时序数据对不上。"""
    lines = render_metric("m", 1, labels={"b": "2", "a": "1"})
    assert lines[-1] == 'm{a="1",b="2"} 1'


def test_render_metric_without_help_omits_comment():
    lines = render_metric("m", 1)
    assert lines == ["# TYPE m gauge", "m 1"]


@pytest.mark.parametrize(
    "value,expected",
    [(1, "1"), (0, "0"), (1.5, "1.5000"), (True, "1"), (False, "0"), (100, "100")],
)
def test_value_formatting(value, expected):
    """整数不带小数点，浮点保留有限精度；布尔不能渲染成 Python 的 True/False。"""
    assert _fmt(value) == expected


def test_label_escaping():
    """反斜杠、引号、换行必须转义，否则会破坏文本格式、导致抓取失败。"""
    assert _escape_label('a"b') == 'a\\"b'
    assert _escape_label("a\\b") == "a\\\\b"
    assert _escape_label("a\nb") == "a\\nb"


# --------------------------------------------------------------------------- #
# 抓取鉴权
# --------------------------------------------------------------------------- #
def test_scrape_denied_in_production_without_token(monkeypatch):
    """生产环境未配置任何凭据时拒绝 —— 不能让指标端点裸奔。"""
    monkeypatch.setattr("app.config.settings.metrics_bearer_token", "", raising=False)
    monkeypatch.setattr("app.config.settings.metrics_public", False, raising=False)
    monkeypatch.setattr("app.config.settings.environment", "production", raising=False)
    allowed, reason = authorize_scrape(None, "127.0.0.1")
    assert allowed is False
    assert "未配置" in reason

    from app.api.routes.metrics import metrics_config_warning

    warning = metrics_config_warning()
    assert warning and "SUPERVISION_METRICS_BEARER_TOKEN" in warning


def test_scrape_allowed_in_dev_without_token(monkeypatch):
    """开发环境未配置凭据时允许匿名抓取。

    这是刻意的可用性取舍：若默认拒绝，`docker compose up` 后 Prometheus
    会一直 403、面板无数据，用户得先做一轮配置才能看到指标。
    """
    monkeypatch.setattr("app.config.settings.metrics_bearer_token", "", raising=False)
    monkeypatch.setattr("app.config.settings.metrics_public", False, raising=False)
    monkeypatch.setattr("app.config.settings.environment", "dev", raising=False)
    assert authorize_scrape(None, "127.0.0.1")[0] is True


def test_scrape_allowed_with_correct_bearer(monkeypatch):
    monkeypatch.setattr("app.config.settings.metrics_bearer_token", "secret-token", raising=False)
    monkeypatch.setattr("app.config.settings.metrics_public", False, raising=False)
    monkeypatch.setattr("app.config.settings.environment", "production", raising=False)
    assert authorize_scrape("Bearer secret-token", "127.0.0.1")[0] is True
    # 大小写不敏感（HTTP 头本身不区分大小写）
    assert authorize_scrape("bearer secret-token", "127.0.0.1")[0] is True


def test_scrape_denied_with_wrong_bearer(monkeypatch):
    monkeypatch.setattr("app.config.settings.metrics_bearer_token", "secret-token", raising=False)
    assert authorize_scrape("Bearer wrong", "127.0.0.1")[0] is False
    assert authorize_scrape(None, "127.0.0.1")[0] is False


def test_scrape_allowed_when_public(monkeypatch):
    monkeypatch.setattr("app.config.settings.metrics_bearer_token", "", raising=False)
    monkeypatch.setattr("app.config.settings.metrics_public", True, raising=False)
    monkeypatch.setattr("app.config.settings.environment", "production", raising=False)
    assert authorize_scrape(None, "10.0.0.5")[0] is True


def test_token_takes_precedence_over_public(monkeypatch):
    """配了令牌就走令牌校验，public 不能成为绕过口子。"""
    monkeypatch.setattr("app.config.settings.metrics_bearer_token", "t", raising=False)
    monkeypatch.setattr("app.config.settings.metrics_public", True, raising=False)
    assert authorize_scrape(None, "127.0.0.1")[0] is False


# --------------------------------------------------------------------------- #
# 端点行为
# --------------------------------------------------------------------------- #
def test_metrics_endpoint_returns_prometheus_text(client, requires_db, monkeypatch):
    """开启匿名抓取后，端点必须返回可解析的 Prometheus 文本。

    需要数据库：指标来自库中数据（文档数/任务数/Token 等）。
    """
    monkeypatch.setattr("app.config.settings.metrics_public", True, raising=False)
    monkeypatch.setattr("app.config.settings.metrics_bearer_token", "", raising=False)

    response = client.get("/metrics")
    assert response.status_code == 200, response.text
    assert "text/plain" in response.headers["content-type"]
    body = response.text

    # 必须含标准注释与样本行
    assert "# HELP supervision_documents_total" in body
    assert "# TYPE supervision_documents_total gauge" in body
    import re

    assert re.search(r"^supervision_documents_total \d+$", body, re.M), "缺少样本行"

    # 不能是 JSON（这是原配置失效的根因）
    assert not body.lstrip().startswith("{")


def test_metrics_degrades_to_error_marker_when_collection_fails(monkeypatch):
    """采集失败时返回错误标记而非 500。

    500 会让 Prometheus 丢弃历史、触发抓取失败告警，反而掩盖真实原因；
    返回可解析的文本并带上错误标记，问题在指标里就能看到。
    """
    from app.api.routes import metrics as metrics_module

    def boom(_session):
        raise RuntimeError("simulated failure")

    monkeypatch.setattr(metrics_module, "collect_metrics", boom)

    from fastapi.testclient import TestClient

    from app.main import app

    monkeypatch.setattr("app.config.settings.metrics_public", True, raising=False)
    monkeypatch.setattr("app.config.settings.metrics_bearer_token", "", raising=False)
    with TestClient(app) as test_client:
        response = test_client.get("/metrics")

    assert response.status_code == 200
    assert "supervision_metrics_scrape_error 1" in response.text


def test_metrics_endpoint_hidden_from_openapi(client):
    """指标端点不进 OpenAPI 文档，避免暴露内部抓取接口。"""
    # 注意：OpenAPI 挂在 API 前缀下（/api/v1/openapi.json），不是根路径
    schema = client.get("/api/v1/openapi.json").json()
    assert "paths" in schema, schema
    assert "/metrics" not in schema["paths"], "根 /metrics 不应出现在 OpenAPI"
    assert "/api/v1/metrics" in schema["paths"], "前端用的 JSON 版应保留"


def test_metrics_endpoint_denied_in_production_without_credentials(client, monkeypatch):
    """生产环境未配置凭据时端点返回 403，并给出配置提示（而非静默开放）。"""
    monkeypatch.setattr("app.config.settings.metrics_public", False, raising=False)
    monkeypatch.setattr("app.config.settings.metrics_bearer_token", "", raising=False)
    monkeypatch.setattr("app.config.settings.environment", "production", raising=False)
    response = client.get("/metrics")
    assert response.status_code == 403
    # 403 也要返回文本（Prometheus 会忽略内容），并给出配置提示
    assert "配置方式" in response.text


def test_frontend_metrics_endpoint_still_requires_auth(client):
    """/api/v1/metrics 仍受 JWT 保护（前端页面用），未被指标端点取代。"""
    response = client.get("/api/v1/metrics")
    assert response.status_code in (401, 403)
