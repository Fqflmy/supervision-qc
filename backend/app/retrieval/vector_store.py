# -*- coding: utf-8 -*-
"""FAISS 向量库（SRS 5.3）。

- 索引类型：向量数 ≤ faiss_flat_threshold 用 IndexFlatIP，否则 IVF-Flat(nlist, nprobe)；
- 向量 L2 归一化后使用内积等价于余弦相似度；
- 按知识库/专业分片（namespace），支持增量写入、删除与原子切换（热更新）；
- 索引文件 + ID 映射表持久化到 faiss_index_dir，支持快照与回滚。
"""
from __future__ import annotations

import json
import math
import shutil
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from app.config import settings
from app.core.errors import VectorSearchError
from app.core.logging_conf import get_logger

logger = get_logger(__name__)


@dataclass
class VectorHit:
    chunk_id: int
    score: float
    rank: int = 0
    meta: dict = field(default_factory=dict)


def _l2_normalize(vectors: Sequence[Sequence[float]]) -> list[list[float]]:
    out: list[list[float]] = []
    for vec in vectors:
        norm = math.sqrt(sum(float(v) * float(v) for v in vec))
        out.append([float(v) / norm for v in vec] if norm > 0 else [0.0] * len(vec))
    return out


class FaissIndex:
    """单个分片的 FAISS 索引。"""

    def __init__(self, name: str, dim: Optional[int] = None) -> None:
        self.name = name
        self.dim = dim or settings.embedding_dim
        self._index = None
        self._ids: list[int] = []
        self._meta: dict[int, dict] = {}
        self._lock = threading.RLock()
        self._dirty = False

    # ---------------- 构建 ----------------
    def _new_index(self, dim: int, expected: int = 0):
        import faiss

        use_ivf = (
            settings.faiss_index_type == "ivf" or expected > settings.faiss_flat_threshold
        )
        if use_ivf and expected > 0:
            quantizer = faiss.IndexFlatIP(dim)
            index = faiss.IndexIVFFlat(
                quantizer, dim, min(settings.faiss_ivf_nlist, max(1, expected // 39 + 1)),
                faiss.METRIC_INNER_PRODUCT,
            )
            index.nprobe = settings.faiss_ivf_nprobe
            return index, True
        return faiss.IndexFlatIP(dim), False

    def _ensure(self, dim: int, expected: int = 0) -> None:
        if self._index is None:
            self._index, needs_train = self._new_index(dim, expected)
            self.dim = dim
            self._needs_train = needs_train
        elif self._index.d != dim:
            raise VectorSearchError(f"向量维度不一致：索引 {self._index.d}，输入 {dim}")

    def add(
        self,
        vectors: Sequence[Sequence[float]],
        chunk_ids: Sequence[int],
        metas: Optional[Sequence[dict]] = None,
    ) -> list[int]:
        import numpy as np

        if len(vectors) != len(chunk_ids):
            raise VectorSearchError("向量数与 ID 数不一致")
        if not vectors:
            return []
        with self._lock:
            self._ensure(len(vectors[0]), expected=len(self._ids) + len(vectors))
            matrix = np.asarray(_l2_normalize(vectors), dtype="float32")
            if getattr(self, "_needs_train", False) and not self._index.is_trained:
                self._index.train(matrix)
                self._needs_train = False
            self._index.add(matrix)
            self._ids.extend(int(cid) for cid in chunk_ids)
            if metas:
                for cid, meta in zip(chunk_ids, metas):
                    self._meta[int(cid)] = meta or {}
            self._dirty = True
            return list(chunk_ids)

    def remove(self, chunk_ids: Sequence[int]) -> int:
        """FAISS 不支持原地删除，按剩余向量重建（SRS FR-KB-10 增量更新）。"""
        drop = {int(c) for c in chunk_ids}
        with self._lock:
            if self._index is None:
                return 0
            keep = [(i, cid) for i, cid in enumerate(self._ids) if cid not in drop]
            if len(keep) == len(self._ids):
                return 0
            import numpy as np

            vectors = self._index.reconstruct_n(0, self._index.ntotal) if keep else None
            self._index = None
            self._ids = []
            metas = self._meta
            self._meta = {}
            if keep and vectors is not None:
                self.add(
                    [vectors[i].tolist() for i, _ in keep],
                    [cid for _, cid in keep],
                    [metas.get(cid, {}) for _, cid in keep],
                )
            return len(drop)

    def search(self, vector: Sequence[float], top_k: int = 50) -> list[VectorHit]:
        import numpy as np

        with self._lock:
            if self._index is None or self._index.ntotal == 0 or not self._ids:
                return []
            query = np.asarray(_l2_normalize([vector]), dtype="float32")
            k = min(top_k, self._index.ntotal)
            scores, indices = self._index.search(query, k)
            hits: list[VectorHit] = []
            for rank, (score, idx) in enumerate(zip(scores[0], indices[0]), start=1):
                if idx < 0 or idx >= len(self._ids):
                    continue
                chunk_id = self._ids[int(idx)]
                hits.append(
                    VectorHit(
                        chunk_id=chunk_id,
                        score=float(score),
                        rank=rank,
                        meta=self._meta.get(chunk_id, {}),
                    )
                )
            return hits

    @property
    def size(self) -> int:
        return self._index.ntotal if self._index is not None else 0

    # ---------------- 持久化 ----------------
    def _base(self) -> Path:
        return Path(settings.faiss_index_dir) / self.name

    def save(self) -> None:
        import faiss

        with self._lock:
            if self._index is None:
                return
            base = self._base()
            base.mkdir(parents=True, exist_ok=True)
            tmp = base / f"index.{int(time.time() * 1000)}.tmp"
            faiss.write_index(self._index, str(tmp))
            target = base / "index.faiss"
            if target.exists():
                target.unlink()
            tmp.replace(target)
            (base / "meta.json").write_text(
                json.dumps(
                    {
                        "name": self.name,
                        "dim": self._index.d,
                        "size": self._index.ntotal,
                        "ids": self._ids,
                        "meta": {str(k): v for k, v in self._meta.items()},
                        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            self._dirty = False

    def load(self) -> bool:
        import faiss

        base = self._base()
        index_file = base / "index.faiss"
        meta_file = base / "meta.json"
        if not index_file.exists() or not meta_file.exists():
            return False
        with self._lock:
            self._index = faiss.read_index(str(index_file))
            payload = json.loads(meta_file.read_text(encoding="utf-8"))
            self._ids = [int(cid) for cid in payload.get("ids", [])]
            self._meta = {int(k): v for k, v in (payload.get("meta") or {}).items()}
            self.dim = int(payload.get("dim") or self._index.d)
        return True

    def snapshot(self, tag: Optional[str] = None) -> Optional[Path]:
        base = self._base()
        if not base.exists():
            return None
        tag = tag or time.strftime("%Y%m%d%H%M%S")
        target = base.parent / "_snapshots" / f"{self.name}_{tag}"
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(base, target)
        return target


_indexes: dict[str, FaissIndex] = {}
_registry_lock = threading.Lock()


def get_faiss_index(name: str = "default") -> FaissIndex:
    with _registry_lock:
        if name not in _indexes:
            index = FaissIndex(name)
            index.load()
            _indexes[name] = index
        return _indexes[name]


def reset_faiss_indexes() -> None:
    with _registry_lock:
        _indexes.clear()


def discover_namespaces() -> list[str]:
    """扫描索引目录，返回已存在的分片名。

    多知识库场景下检索需要跨分片召回（每个知识库一个 FAISS 分片）。
    """
    root = Path(settings.faiss_index_dir)
    if not root.exists():
        return []
    return sorted(
        child.name
        for child in root.iterdir()
        if child.is_dir() and (child / "index.faiss").exists() and not child.name.startswith("_")
    )


def index_stats(name: str = "default") -> dict[str, Any]:
    index = get_faiss_index(name)
    return {"name": name, "size": index.size, "dim": index.dim, "dirty": index._dirty}


def all_index_stats() -> dict[str, Any]:
    namespaces = discover_namespaces()
    return {
        "namespaces": {name: index_stats(name) for name in namespaces},
        "total": len(namespaces),
    }


__all__ = [
    "FaissIndex",
    "VectorHit",
    "get_faiss_index",
    "reset_faiss_indexes",
    "discover_namespaces",
    "index_stats",
    "all_index_stats",
]
