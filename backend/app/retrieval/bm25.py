# -*- coding: utf-8 -*-
"""BM25 稀疏检索（SRS FR-RET-04）。

中文使用 jieba 分词；索引常驻内存并持久化到磁盘，支持增量重建。
"""
from __future__ import annotations

import json
import pickle
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from app.config import settings
from app.core.logging_conf import get_logger

logger = get_logger(__name__)

_TOKEN_RE = re.compile(r"[a-zA-Z0-9]+")
_STOPWORDS = {
    "的", "了", "和", "与", "及", "或", "在", "是", "为", "对", "应", "按", "并",
    "其", "之", "等", "有", "不", "中", "上", "下", "内", "外", "本", "该", "可",
}


@dataclass
class Bm25Hit:
    chunk_id: int
    score: float
    rank: int = 0


@dataclass
class Bm25Document:
    chunk_id: int
    text: str
    clause_no: Optional[str] = None
    doc_id: Optional[int] = None
    meta: dict = field(default_factory=dict)


def tokenize(text: str) -> list[str]:
    """中文 jieba + 英文数字保留，过滤停用词与单字。"""
    if not text:
        return []
    import jieba

    jieba.setLogLevel(60)
    tokens: list[str] = []
    for raw in jieba.lcut(text.lower()):
        token = raw.strip()
        if not token:
            continue
        if _TOKEN_RE.fullmatch(token):
            tokens.append(token)
            continue
        if len(token) >= 2 and token not in _STOPWORDS:
            tokens.append(token)
    return tokens


class Bm25Index:
    """内存 BM25 索引，支持按知识库分片持久化。"""

    def __init__(self, name: str = "default") -> None:
        self.name = name
        self._lock = threading.RLock()
        self._docs: list[Bm25Document] = []
        self._tokens: list[list[str]] = []
        self._bm25 = None
        self._id_to_pos: dict[int, int] = {}

    # ---------------- 构建 ----------------
    def build(self, documents: Sequence[Bm25Document]) -> None:
        from rank_bm25 import BM25Okapi

        with self._lock:
            self._docs = list(documents)
            self._tokens = [tokenize(d.text) for d in self._docs]
            self._id_to_pos = {d.chunk_id: i for i, d in enumerate(self._docs)}
            corpus = [t or ["_empty_"] for t in self._tokens]
            self._bm25 = BM25Okapi(corpus) if corpus else None
            logger.info(
                "BM25 索引构建完成",
                extra={"index": self.name, "docs": len(self._docs)},
            )

    def add(self, documents: Sequence[Bm25Document]) -> None:
        """增量追加：重建索引（BM25 统计量依赖全量语料）。"""
        existing = {d.chunk_id for d in self._docs}
        merged = list(self._docs) + [d for d in documents if d.chunk_id not in existing]
        self.build(merged)

    def remove(self, chunk_ids: Sequence[int]) -> None:
        drop = set(chunk_ids)
        self.build([d for d in self._docs if d.chunk_id not in drop])

    # ---------------- 检索 ----------------
    def search(self, query: str, top_k: Optional[int] = None) -> list[Bm25Hit]:
        top_k = top_k or settings.bm25_top_k
        with self._lock:
            if self._bm25 is None or not self._docs:
                return []
            query_tokens = tokenize(query)
            if not query_tokens:
                return []
            scores = self._bm25.get_scores(query_tokens)
            ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            hits: list[Bm25Hit] = []
            for rank, pos in enumerate(ranked[:top_k], start=1):
                score = float(scores[pos])
                if score <= 0:
                    continue
                hits.append(Bm25Hit(chunk_id=self._docs[pos].chunk_id, score=score, rank=rank))
            return hits

    @property
    def size(self) -> int:
        return len(self._docs)

    def get_document(self, chunk_id: int) -> Optional[Bm25Document]:
        pos = self._id_to_pos.get(chunk_id)
        return self._docs[pos] if pos is not None else None

    # ---------------- 持久化 ----------------
    def _path(self) -> Path:
        return Path(settings.faiss_index_dir) / f"bm25_{self.name}.pkl"

    def save(self) -> None:
        path = self._path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            payload = {"docs": self._docs, "tokens": self._tokens}
        with path.open("wb") as fh:
            pickle.dump(payload, fh)
        meta = path.with_suffix(".meta.json")
        meta.write_text(
            json.dumps({"index": self.name, "docs": len(payload["docs"])}, ensure_ascii=False),
            encoding="utf-8",
        )

    def load(self) -> bool:
        path = self._path()
        if not path.exists():
            return False
        try:
            with path.open("rb") as fh:
                payload = pickle.load(fh)
        except Exception as exc:  # noqa: BLE001
            logger.warning("BM25 索引加载失败", extra={"error": str(exc)[:200]})
            return False
        self.build(payload.get("docs", []))
        return True


_indexes: dict[str, Bm25Index] = {}
_index_lock = threading.Lock()


def get_bm25_index(name: str = "default") -> Bm25Index:
    with _index_lock:
        if name not in _indexes:
            index = Bm25Index(name)
            index.load()
            _indexes[name] = index
        return _indexes[name]


def reset_bm25_indexes() -> None:
    with _index_lock:
        _indexes.clear()


__all__ = [
    "Bm25Index",
    "Bm25Document",
    "Bm25Hit",
    "tokenize",
    "get_bm25_index",
    "reset_bm25_indexes",
]
