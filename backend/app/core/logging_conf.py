# -*- coding: utf-8 -*-
"""结构化日志 + trace_id 贯穿（SRS 7.4）。"""
from __future__ import annotations

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar
from typing import Any, Optional

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")

_RESERVED = {
    "name", "msg", "args", "levelname", "levelno", "pathname", "filename",
    "module", "exc_info", "exc_text", "stack_info", "lineno", "funcName",
    "created", "msecs", "relativeCreated", "thread", "threadName",
    "processName", "process", "taskName", "message",
}


def new_trace_id() -> str:
    return uuid.uuid4().hex


def set_trace_id(trace_id: Optional[str] = None) -> str:
    tid = trace_id or new_trace_id()
    _trace_id.set(tid)
    return tid


def get_trace_id() -> str:
    tid = _trace_id.get()
    if not tid:
        tid = set_trace_id()
    return tid


class TraceIdFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.trace_id = get_trace_id()
        return True


class JsonFormatter(logging.Formatter):
    """单行 JSON 日志，便于 Loki/ELK 采集。"""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
            + f".{int(record.msecs):03d}",
            "level": record.levelname,
            "logger": record.name,
            "trace_id": getattr(record, "trace_id", ""),
            "msg": record.getMessage(),
        }
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                try:
                    json.dumps(value, ensure_ascii=False)
                    payload[key] = value
                except (TypeError, ValueError):
                    payload[key] = repr(value)
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class PlainFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        record.trace_id = getattr(record, "trace_id", "")
        return (
            f"{self.formatTime(record, '%H:%M:%S')} {record.levelname:<7} "
            f"[{record.trace_id[:8]}] {record.name}: {record.getMessage()}"
        )


_configured = False


def setup_logging(level: str = "INFO", json_output: bool = True) -> None:
    global _configured
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if _configured:
        return
    for handler in list(root.handlers):
        root.removeHandler(handler)
    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(TraceIdFilter())
    handler.setFormatter(JsonFormatter() if json_output else PlainFormatter())
    root.addHandler(handler)
    for noisy in (
        "httpx",
        "httpcore",
        "neo4j",
        "neo4j.notifications",
        "urllib3",
        "asyncio",
        "faiss",
        "sentence_transformers",
        "transformers",
    ):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    _configured = True


class SafeLogger(logging.Logger):
    """防御性 Logger：把 ``extra`` 中的保留键自动改名为 ``field_<key>``。

    ``logging.Logger.makeRecord`` 遇到与 LogRecord 同名的 extra 键会抛 KeyError，
    直接把业务请求打成 500。这里统一兜底，保证日志永远不会成为故障源。
    """

    def makeRecord(  # noqa: PLR0913
        self,
        name,
        level,
        fn,
        lno,
        msg,
        args,
        exc_info,
        func=None,
        extra=None,
        sinfo=None,
    ):
        if extra:
            clashes = [key for key in extra if key in _RESERVED]
            if clashes:
                extra = {
                    (f"field_{key}" if key in _RESERVED else key): value
                    for key, value in extra.items()
                }
        return super().makeRecord(
            name, level, fn, lno, msg, args, exc_info, func, extra, sinfo
        )


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)


# 模块导入即安装防御性 Logger 类：必须早于任何业务模块调用 get_logger()，
# 否则那些 logger 仍是标准 Logger，extra 保留键会抛 KeyError 把请求打成 500。
logging.setLoggerClass(SafeLogger)


__all__ = [
    "setup_logging",
    "get_logger",
    "set_trace_id",
    "get_trace_id",
    "new_trace_id",
]
