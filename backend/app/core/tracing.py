# -*- coding: utf-8 -*-
"""LangSmith 链路追踪接入。

设计要点
--------
1. **优雅关闭**：未配置 ``LANGSMITH_API_KEY`` 或显式关闭时，本模块所有接口
   都退化为零开销的空操作，不影响任何功能。这一点很重要 —— 追踪属于
   可观测性增强，绝不能让「没配 Key」变成启动失败。
2. **不污染返回值**：``trace_span`` 是上下文管理器，退出时把输出挂到 span 上，
   而不是包装函数返回值。这样无论是否启用追踪，被追踪函数的返回值完全一致。
3. **可控的信息采集**：``langsmith_capture_content`` 关闭时，只上报形状
   （长度、条数、模型名）而**不采集提示词与文档正文**。工程资料可能含敏感信息，
   默认开启采集但提供开关。
4. **环境变量双通道**：既支持标准的 ``LANGSMITH_*`` 环境变量（langsmith SDK 直接读取），
   也支持项目风格的 ``SUPERVISION_LANGSMITH_*``（由 pydantic-settings 读取，
   便于写入 ``deploy/.env`` 与 compose）。

为什么必须显式接入
------------------
本项目调用大模型用的是**原生 openai SDK**（``AsyncOpenAI``），不是 LangChain 的
ChatModel；Agent 图虽然基于 LangGraph，但节点内部是自定义的 ``await`` 逻辑。
因此**仅设置 ``LANGSMITH_TRACING=true`` 不会产生任何追踪数据**，必须显式使用
``traceable`` / ``trace``。这也是很多项目「配了环境变量却看不到数据」的原因。
"""
from __future__ import annotations

import contextlib
import os
from dataclasses import dataclass
from typing import Any, Iterator, Optional, Sequence

from app.config import settings
from app.core.logging_conf import get_logger

logger = get_logger(__name__)

#: 全局开关缓存：首次求值后固定，避免每次调用都做判定
_state: Optional["TracingState"] = None


@dataclass(frozen=True)
class TracingState:
    """追踪运行状态。"""

    enabled: bool
    project: str
    #: 是否采集提示词/正文（False 时仅上报形状）
    capture_content: bool
    #: 关闭原因（用于启动日志说明「为什么没有追踪」）
    reason: str = ""


def _resolve_state() -> TracingState:
    """判定是否启用追踪，并返回状态。"""
    enabled_flag = bool(settings.langsmith_tracing)
    api_key = (settings.langsmith_api_key or os.environ.get("LANGSMITH_API_KEY") or "").strip()
    project = (settings.langsmith_project or "supervision-qc").strip()
    capture = bool(settings.langsmith_capture_content)

    if not enabled_flag:
        return TracingState(False, project, capture, "未启用（langsmith_tracing=False）")
    if not api_key:
        return TracingState(
            False, project, capture, "未配置 LANGSMITH_API_KEY（追踪自动关闭，功能不受影响）"
        )
    try:
        import langsmith  # noqa: F401
    except Exception as exc:  # noqa: BLE001
        return TracingState(False, project, capture, f"langsmith 未安装：{type(exc).__name__}")

    # langsmith SDK 从环境变量读取配置；这里把项目配置同步过去，
    # 使「SUPERVISION_LANGSMITH_* 写在 .env 里」也能生效。
    os.environ.setdefault("LANGSMITH_API_KEY", api_key)
    os.environ.setdefault("LANGSMITH_PROJECT", project)
    os.environ.setdefault("LANGSMITH_TRACING", "true")
    # 兼容旧变量名（部分工具链仍读 LANGCHAIN_*）
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "true")
    os.environ.setdefault("LANGCHAIN_PROJECT", project)
    os.environ.setdefault("LANGCHAIN_API_KEY", api_key)
    return TracingState(True, project, capture, "")


def get_state() -> TracingState:
    """获取（并缓存）追踪状态。"""
    global _state
    if _state is None:
        _state = _resolve_state()
    return _state


def reset_state() -> None:
    """清除缓存（测试用）。"""
    global _state
    _state = None


def is_enabled() -> bool:
    return get_state().enabled


def startup_message() -> str:
    """启动时打印的追踪状态说明。"""
    state = get_state()
    if state.enabled:
        mode = "含提示词与正文" if state.capture_content else "仅形状（不采集正文）"
        return f"LangSmith 追踪已启用：project={state.project}，采集范围={mode}"
    return f"LangSmith 追踪未启用：{state.reason}"


def _shape(value: Any, limit: int = 200) -> Any:
    """把输入/输出压成「形状」摘要，避免把长正文或敏感内容写到追踪里。"""
    if value is None or isinstance(value, (int, float, bool)):
        return value
    if isinstance(value, str):
        text = value if len(value) <= limit else value[:limit] + f"…(len={len(value)})"
        return text
    if isinstance(value, dict):
        return {str(k): _shape(v, limit) for k, v in list(value.items())[:20]}
    if isinstance(value, (list, tuple)):
        items = [_shape(v, limit) for v in list(value)[:10]]
        if len(value) > 10:
            items.append(f"…(共 {len(value)} 项)")
        return items
    return f"<{type(value).__name__}>"


class Span:
    """追踪 span 句柄。

    直接在上下文里写 ``outputs``，退出时自动上报。也可以由包装函数
    ``with trace_span(...) as span: return span.done(await impl(...))`` 记录返回值。
    """

    __slots__ = ("_run", "_capture", "inputs", "outputs", "metadata")

    def __init__(self, run: Any, capture: bool, inputs: dict, metadata: dict) -> None:
        self._run = run
        self._capture = capture
        self.inputs = inputs
        self.outputs: Any = None
        self.metadata = metadata

    def done(self, result: Any) -> Any:
        """记录返回值并原样返回，便于 ``return span.done(x)`` 写法。"""
        self.outputs = result
        return result

    def _flush(self) -> None:
        if self._run is None or self.outputs is None:
            return
        outputs = self.outputs if self._capture else _shape(self.outputs)
        with contextlib.suppress(Exception):
            self._run.end(outputs=outputs)


@contextlib.contextmanager
def trace_span(
    name: str,
    *,
    run_type: str = "chain",
    inputs: Optional[dict] = None,
    metadata: Optional[dict] = None,
    tags: Optional[Sequence[str]] = None,
) -> Iterator[Span]:
    """追踪一段逻辑。

    用法::

        with trace_span("retrieval", run_type="retriever", inputs={"query": q}) as span:
            result = await pipeline.retrieve(q, ...)
            span.outputs = {"clauses": len(result.clauses)}

    未启用追踪时是空操作（``span`` 仍可用，写入被丢弃）。
    任何追踪异常都被吞掉并降级为空操作 —— 可观测性绝不能影响主流程。
    """
    state = get_state()
    if not state.enabled:
        yield Span(None, state.capture_content, inputs or {}, metadata or {})
        return

    payload_inputs = inputs or {}
    if not state.capture_content:
        payload_inputs = _shape(payload_inputs)

    try:
        from langsmith import trace

        with trace(
            name=name,
            run_type=run_type,
            inputs=payload_inputs,
            extra={"metadata": metadata or {}},
            tags=list(tags or []),
            project_name=state.project,
        ) as run:
            span = Span(run, state.capture_content, payload_inputs, metadata or {})
            try:
                yield span
            finally:
                span._flush()
    except Exception as exc:  # noqa: BLE001 - 追踪失败绝不影响主流程
        logger.debug("追踪 span 创建失败，降级为空操作", extra={"span": name, "error": str(exc)[:200]})
        yield Span(None, state.capture_content, inputs or {}, metadata or {})


def annotate(**metadata: Any) -> None:
    """给当前 span 附加元数据（未启用或不在 span 内时为空操作）。"""
    if not get_state().enabled:
        return
    try:
        from langsmith import get_current_run_tree

        run = get_current_run_tree()
        if run is not None:
            clean = {k: v for k, v in metadata.items() if v is not None}
            if clean:
                run.add_metadata(clean)
    except Exception:  # noqa: BLE001
        return


def current_run_id() -> Optional[str]:
    """当前追踪 run 的 ID（可用于把业务记录与追踪关联起来）。"""
    if not get_state().enabled:
        return None
    try:
        from langsmith import get_current_run_tree

        run = get_current_run_tree()
        return str(run.id) if run is not None else None
    except Exception:  # noqa: BLE001
        return None


def traced(name: str, *, run_type: str = "chain", tags: Optional[Sequence[str]] = None):
    """装饰器：把函数调用包装成一个 span。

    与 ``trace_span`` 的差别：这里自动把入参与返回值挂到 span 上，
    适合「参数即追踪输入、返回值即追踪输出」的简单场景。
    未启用追踪时返回原函数，零开销。
    """
    state = get_state()
    if not state.enabled:
        def _passthrough(func):
            return func

        return _passthrough

    def _decorator(func):
        import functools
        import inspect

        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def _async_wrapper(*args: Any, **kwargs: Any) -> Any:
                with trace_span(
                    name,
                    run_type=run_type,
                    inputs={"args": list(args[1:]), "kwargs": kwargs},
                    tags=tags,
                ) as span:
                    result = await func(*args, **kwargs)
                    span["outputs"] = result
                    return result

            return _async_wrapper

        @functools.wraps(func)
        def _sync_wrapper(*args: Any, **kwargs: Any) -> Any:
            with trace_span(
                name, run_type=run_type, inputs={"args": list(args[1:]), "kwargs": kwargs}, tags=tags
            ) as span:
                result = func(*args, **kwargs)
                span["outputs"] = result
                return result

        return _sync_wrapper

    return _decorator
