# -*- coding: utf-8 -*-
"""Token 计数：优先 tiktoken；中文场景退化为字符估算。"""
from __future__ import annotations

from functools import lru_cache
from typing import Optional

_encoder = None
_encoder_ready = False


def _get_encoder():
    global _encoder, _encoder_ready
    if _encoder_ready:
        return _encoder
    _encoder_ready = True
    try:
        import tiktoken

        _encoder = tiktoken.get_encoding("cl100k_base")
    except Exception:  # noqa: BLE001 - 离线环境无缓存时降级
        _encoder = None
    return _encoder


@lru_cache(maxsize=4096)
def count_tokens(text: Optional[str]) -> int:
    """估算 token 数。中文约 1 字 ≈ 1 token，英文约 4 字符 ≈ 1 token。"""
    if not text:
        return 0
    encoder = _get_encoder()
    if encoder is not None:
        try:
            return len(encoder.encode(text, disallowed_special=()))
        except Exception:  # noqa: BLE001
            pass
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    others = len(text) - cjk
    return cjk + max(1, others // 4)


def truncate_to_tokens(text: str, max_tokens: int) -> str:
    if count_tokens(text) <= max_tokens:
        return text
    encoder = _get_encoder()
    if encoder is not None:
        try:
            return encoder.decode(encoder.encode(text, disallowed_special=())[:max_tokens])
        except Exception:  # noqa: BLE001
            pass
    ratio = max_tokens / max(1, count_tokens(text))
    return text[: max(1, int(len(text) * ratio))]


__all__ = ["count_tokens", "truncate_to_tokens"]
