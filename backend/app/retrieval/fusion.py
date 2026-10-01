# -*- coding: utf-8 -*-
"""RRF 倒数排名融合（SRS FR-RET-05）。

RRF(d) = Σ_i w_i / (k + rank_i(d))，k 默认 60。
支持多路子查询 × 双通道（BM25 / 向量）任意条召回列表的融合。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Optional, Sequence


@dataclass
class RankedItem:
    chunk_id: int
    score: float
    rank: int
    source: str


@dataclass
class FusedItem:
    chunk_id: int
    rrf_score: float
    sources: list[str] = field(default_factory=list)
    best_rank: int = 10**6


def rrf_fuse(
    ranked_lists: Sequence[Sequence[RankedItem]],
    *,
    weights: Optional[Sequence[float]] = None,
    k: int = 60,
    top_k: Optional[int] = None,
) -> list[FusedItem]:
    """融合多路召回结果。

    Args:
        ranked_lists: 每路召回的排序列表（已按相关性降序）。
        weights: 每路权重，默认 1.0；长度需与 ranked_lists 一致。
        k: RRF 平滑常数。
        top_k: 截断数量。
    """
    if not ranked_lists:
        return []
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    if len(weights) != len(ranked_lists):
        raise ValueError("weights 数量与召回列表数量不一致")

    accumulated: dict[int, FusedItem] = {}
    for items, weight in zip(ranked_lists, weights):
        if not items:
            continue
        for item in items:
            contribution = float(weight) / (k + max(1, item.rank))
            entry = accumulated.get(item.chunk_id)
            if entry is None:
                entry = FusedItem(chunk_id=item.chunk_id, rrf_score=0.0)
                accumulated[item.chunk_id] = entry
            entry.rrf_score += contribution
            if item.source not in entry.sources:
                entry.sources.append(item.source)
            entry.best_rank = min(entry.best_rank, item.rank)

    fused = sorted(
        accumulated.values(),
        key=lambda e: (-e.rrf_score, e.best_rank, e.chunk_id),
    )
    return fused[:top_k] if top_k else fused


def weighted_rrf(
    channels: dict[str, Sequence[RankedItem]],
    channel_weights: dict[str, float],
    *,
    k: int = 60,
    top_k: Optional[int] = None,
) -> list[FusedItem]:
    """按通道字典融合，便于配置 bm25_weight / dense_weight。"""
    lists: list[Sequence[RankedItem]] = []
    weights: list[float] = []
    for name, items in channels.items():
        lists.append(items)
        weights.append(float(channel_weights.get(name, 1.0)))
    return rrf_fuse(lists, weights=weights, k=k, top_k=top_k)


def normalize_scores(items: Iterable[RankedItem]) -> list[RankedItem]:
    """把原始分数线性归一化到 [0,1]，保留排序。"""
    items = list(items)
    if not items:
        return []
    scores = [i.score for i in items]
    lo, hi = min(scores), max(scores)
    if hi - lo < 1e-12:
        return [RankedItem(i.chunk_id, 1.0, i.rank, i.source) for i in items]
    return [RankedItem(i.chunk_id, (i.score - lo) / (hi - lo), i.rank, i.source) for i in items]


__all__ = ["RankedItem", "FusedItem", "rrf_fuse", "weighted_rrf", "normalize_scores"]
