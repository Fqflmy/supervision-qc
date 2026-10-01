# -*- coding: utf-8 -*-
"""统一响应体（SRS 6.1）。"""
from __future__ import annotations

from typing import Any, Generic, Optional, TypeVar

from pydantic import BaseModel, Field

from app.core.logging_conf import get_trace_id

T = TypeVar("T")


class Envelope(BaseModel, Generic[T]):
    code: int = 0
    message: str = "success"
    data: Optional[T] = None
    trace_id: str = Field(default_factory=get_trace_id)


def ok(data: Any = None, message: str = "success") -> dict:
    return {
        "code": 0,
        "message": message,
        "data": data,
        "trace_id": get_trace_id(),
    }


def fail(code: int, message: str, details: Any = None) -> dict:
    return {
        "code": code,
        "message": message,
        "data": details,
        "trace_id": get_trace_id(),
    }


class PageMeta(BaseModel):
    page: int = 1
    page_size: int = 20
    total: int = 0
    pages: int = 0


class PageData(BaseModel, Generic[T]):
    items: list[T] = Field(default_factory=list)
    meta: PageMeta = Field(default_factory=PageMeta)


def paginate(items: list, total: int, page: int, page_size: int) -> dict:
    pages = (total + page_size - 1) // page_size if page_size else 0
    return {
        "items": items,
        "meta": {"page": page, "page_size": page_size, "total": total, "pages": pages},
    }


__all__ = ["Envelope", "PageData", "PageMeta", "ok", "fail", "paginate"]
