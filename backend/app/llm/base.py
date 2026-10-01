# -*- coding: utf-8 -*-
"""数据类型定义。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

Role = Literal["system", "user", "assistant"]


@dataclass
class ChatMessage:
    role: Role
    content: str

    def to_dict(self) -> dict[str, str]:
        return {"role": self.role, "content": self.content}


@dataclass
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(
            self.prompt_tokens + other.prompt_tokens,
            self.completion_tokens + other.completion_tokens,
            self.total_tokens + other.total_tokens,
        )

    def as_dict(self) -> dict[str, int]:
        return {
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.total_tokens,
        }


@dataclass
class LlmResponse:
    content: str
    model: str
    provider: str = "openai_compatible"
    usage: Usage = field(default_factory=Usage)
    latency_ms: int = 0
    is_fallback: bool = False
    finish_reason: Optional[str] = None
    raw: Optional[dict[str, Any]] = None


@dataclass
class RerankResult:
    index: int
    score: float


@dataclass
class EmbeddingResult:
    vectors: list[list[float]]
    model: str
    dim: int


__all__ = [
    "ChatMessage",
    "Usage",
    "LlmResponse",
    "RerankResult",
    "EmbeddingResult",
    "Role",
]
