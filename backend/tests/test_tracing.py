# -*- coding: utf-8 -*-
"""LangSmith 追踪接入测试。

重点覆盖「不能让可观测性影响主流程」这条底线：
- 未配置 Key 时必须优雅关闭（而不是报错或拖慢）；
- 追踪未启用时 ``trace_span`` 是空操作；
- 关闭正文采集时只上报形状；
- 追踪内部异常不能冒泡到业务代码。
"""
from __future__ import annotations

import pytest

from app.core import tracing
from app.core.tracing import (
    Span,
    annotate,
    current_run_id,
    get_state,
    is_enabled,
    reset_state,
    startup_message,
    trace_span,
    traced,
    _shape,
)


@pytest.fixture(autouse=True)
def _reset_tracing():
    reset_state()
    yield
    reset_state()


def _patch(monkeypatch, *, tracing_enabled=True, api_key="", project="proj", capture=True, environment="dev"):
    monkeypatch.setattr("app.config.settings.langsmith_tracing", tracing_enabled, raising=False)
    monkeypatch.setattr("app.config.settings.langsmith_api_key", api_key, raising=False)
    monkeypatch.setattr("app.config.settings.langsmith_project", project, raising=False)
    monkeypatch.setattr("app.config.settings.langsmith_capture_content", capture, raising=False)
    monkeypatch.setattr("app.config.settings.environment", environment, raising=False)
    # 清掉 SDK 可能读取的环境变量，避免宿主机配置干扰
    monkeypatch.delenv("LANGSMITH_API_KEY", raising=False)


# --------------------------------------------------------------------------- #
# 关闭逻辑
# --------------------------------------------------------------------------- #
def test_disabled_when_flag_off(monkeypatch):
    _patch(monkeypatch, tracing_enabled=False, api_key="lsv2_key")
    state = get_state()
    assert state.enabled is False
    assert "未启用" in state.reason
    assert is_enabled() is False


def test_disabled_without_api_key(monkeypatch):
    """没配 Key 时必须优雅关闭 —— 追踪是增强能力，不该阻止使用。"""
    _patch(monkeypatch, api_key="")
    state = get_state()
    assert state.enabled is False
    assert "LANGSMITH_API_KEY" in state.reason
    # 启动日志要明确说明原因，省去「配了却没数据」的排查
    assert "未启用" in startup_message()


def test_enabled_with_key(monkeypatch):
    _patch(monkeypatch, api_key="lsv2_pt_fake")
    state = get_state()
    assert state.enabled is True
    assert state.project == "proj"
    assert "已启用" in startup_message()


def test_default_project_name(monkeypatch):
    """未指定项目名时用默认值，避免数据散落到默认项目。"""
    import app.config as cfg

    monkeypatch.delenv("LANGSMITH_PROJECT", raising=False)
    assert cfg.Settings().langsmith_project == "supervision-qc"


# --------------------------------------------------------------------------- #
# 空操作路径
# --------------------------------------------------------------------------- #
def test_trace_span_is_noop_when_disabled(monkeypatch):
    _patch(monkeypatch, api_key="")

    executed = False
    with trace_span("x") as span:
        executed = True
        span.outputs = {"a": 1}
    assert executed is True, "未启用追踪时也必须执行被包裹的逻辑"
    assert isinstance(span, Span)


def test_trace_span_does_not_swallow_business_exceptions(monkeypatch):
    """业务异常必须原样抛出 —— 追踪包装不能把异常吃掉。"""
    _patch(monkeypatch, api_key="")

    class Boom(RuntimeError):
        pass

    with pytest.raises(Boom):
        with trace_span("x"):
            raise Boom("business failure")


def test_traced_decorator_passthrough_when_disabled(monkeypatch):
    """未启用时装饰器不改变函数行为与签名。"""
    _patch(monkeypatch, api_key="")

    calls = []

    @traced("fn")
    def add(a, b):
        calls.append((a, b))
        return a + b

    assert add(1, 2) == 3
    assert calls == [(1, 2)]


def test_traced_decorator_async_passthrough(monkeypatch):
    _patch(monkeypatch, api_key="")

    @traced("fn")
    async def mul(a, b):
        return a * b

    import asyncio

    assert asyncio.run(mul(3, 4)) == 12


def test_annotate_and_run_id_are_safe_when_disabled(monkeypatch):
    _patch(monkeypatch, api_key="")
    annotate(foo="bar")  # 不应抛错
    assert current_run_id() is None


# --------------------------------------------------------------------------- #
# 形状压缩（敏感场景）
# --------------------------------------------------------------------------- #
def test_shape_truncates_long_strings():
    long_text = "x" * 500
    shaped = _shape(long_text, limit=100)
    assert len(shaped) < 500
    assert "len=500" in shaped


def test_shape_limits_collections():
    shaped = _shape(list(range(50)))
    assert len(shaped) <= 11  # 10 项 + 省略标记
    assert "共 50 项" in shaped[-1]


def test_shape_summarizes_unknown_objects():
    class Widget:
        pass

    assert _shape(Widget()) == "<Widget>"


def test_capture_content_flag_is_recorded(monkeypatch):
    _patch(monkeypatch, api_key="lsv2_key", capture=False)
    assert get_state().capture_content is False
    assert "仅形状" in startup_message()


# --------------------------------------------------------------------------- #
# 与真实接入点的一致性
# --------------------------------------------------------------------------- #
def test_key_entrypoints_are_wrapped():
    """确认关键路径确实接了追踪。

    这些是「配了环境变量却看不到数据」的高发点：本项目用的是原生 openai SDK，
    不做显式插桩就不会有任何追踪。
    """
    import inspect

    from app.agent import graph, runner
    from app.llm.gateway import LlmGateway
    from app.retrieval.pipeline import RetrievalPipeline

    assert "trace_span" in inspect.getsource(runner.run_evaluation)
    assert "trace_span" in inspect.getsource(LlmGateway.chat)
    assert "trace_span" in inspect.getsource(RetrievalPipeline.retrieve)
    for node in (
        graph.planning_node,
        graph.retrieval_node,
        graph.matching_node,
        graph.analysis_node,
        graph.report_node,
    ):
        assert "trace_span" in inspect.getsource(node), f"{node.__name__} 未接入追踪"


def test_tracing_failure_does_not_break_business(monkeypatch):
    """追踪内部抛错时应降级为空操作，业务继续执行。"""
    _patch(monkeypatch, api_key="lsv2_key")

    import langsmith

    def boom(*_args, **_kwargs):
        raise RuntimeError("langsmith down")

    monkeypatch.setattr(langsmith, "trace", boom)

    executed = False
    with trace_span("x") as span:
        executed = True
        span.outputs = {"ok": True}
    assert executed is True
