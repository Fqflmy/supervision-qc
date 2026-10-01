# -*- coding: utf-8 -*-
"""Reranker 适配层（SRS FR-RET-06）。

provider：
- ``local``        : sentence-transformers CrossEncoder 加载 BGE-Reranker；
- ``http``         : 调用独立重排服务；
- ``score_fusion`` : 词面+向量分数融合的无模型降级重排。
"""
from __future__ import annotations

import json
import math
import re
import threading
import urllib.request
from typing import Optional, Sequence

from app.config import settings
from app.core.logging_conf import get_logger
from app.llm.base import RerankResult
from app.llm.tokens import count_tokens

logger = get_logger(__name__)

_PUNCT_RE = re.compile(r"[\s，。；：、（）()【】\[\]“”\"'’‘,.;:!?！？/\\|<>《》\-—_]+")


def _lexical_terms(text: str) -> set[str]:
    text = (text or "").lower()
    words = {w for w in _PUNCT_RE.split(text) if len(w) >= 2}
    chars = "".join(ch for ch in text if not ch.isspace())
    for size in (2, 3):
        words.update(chars[i : i + size] for i in range(max(0, len(chars) - size + 1)))
    return words


class BaseReranker:
    name = "base"
    degraded = False

    def rerank(self, query: str, documents: Sequence[str], top_n: Optional[int] = None) -> list[RerankResult]:
        raise NotImplementedError


class ScoreFusionReranker(BaseReranker):
    """无模型降级重排：查询词覆盖率 + 长度惩罚 + 条款号命中加权。

    分数被归一化到 [0,1]，与真实重排模型保持同一量纲，便于阈值（no_evidence_threshold）统一。
    """

    name = "score_fusion"
    degraded = True

    def rerank(self, query: str, documents: Sequence[str], top_n: Optional[int] = None) -> list[RerankResult]:
        query_terms = _lexical_terms(query)
        clause_pattern = re.findall(r"\d+(?:\.\d+)+", query)
        results: list[RerankResult] = []
        for index, doc in enumerate(documents):
            doc_terms = _lexical_terms(doc)
            if not query_terms or not doc_terms:
                overlap = 0.0
            else:
                hit = len(query_terms & doc_terms)
                overlap = hit / math.sqrt(len(query_terms) * len(doc_terms))
            coverage = (
                len(query_terms & doc_terms) / len(query_terms) if query_terms else 0.0
            )
            score = 0.55 * coverage + 0.45 * min(1.0, overlap * 2.2)
            for clause in clause_pattern:
                if clause in doc:
                    score = min(1.0, score + 0.15)
            # 长度惩罚：过短/过长内容降权
            tokens = count_tokens(doc)
            if tokens < 30:
                score *= 0.75
            elif tokens > 1200:
                score *= 0.92
            results.append(RerankResult(index=index, score=round(min(1.0, score), 6)))
        results.sort(key=lambda r: r.score, reverse=True)
        limit = top_n if top_n is not None else settings.reranker_top_n
        return results[:limit]


class LocalReranker(BaseReranker):
    """CrossEncoder 加载 BGE-Reranker（sigmoid 归一化到 0-1）。"""

    def __init__(self, model_path: str, device: str = "cpu") -> None:
        self.model_path = model_path
        self.device = device
        self.name = f"local:{model_path.replace(chr(92), '/').rstrip('/').split('/')[-1]}"
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from sentence_transformers import CrossEncoder

                logger.info("加载 Reranker 模型", extra={"path": self.model_path})
                self._model = CrossEncoder(self.model_path, device=self.device, max_length=512)
            return self._model

    def rerank(self, query: str, documents: Sequence[str], top_n: Optional[int] = None) -> list[RerankResult]:
        model = self._load()
        pairs = [(query, doc) for doc in documents]
        scores = model.predict(pairs, batch_size=settings.reranker_batch_size, show_progress_bar=False)
        results = [
            RerankResult(index=i, score=float(1 / (1 + math.exp(-float(s))))) for i, s in enumerate(scores)
        ]
        results.sort(key=lambda r: r.score, reverse=True)
        return results[: (top_n if top_n is not None else settings.reranker_top_n)]


class SiliconFlowReranker(BaseReranker):
    """硅基流动（SiliconFlow）托管的 Reranker 服务。

    接口：POST {base_url}/rerank
        {"model": "...", "query": "...", "documents": [...]}
      -> {"results": [{"index": 0, "relevance_score": 0.93}, ...]}

    与 HttpReranker 的差异仅在于鉴权与字段名（relevance_score），
    因此单独实现以便兼容两种返回结构。
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "https://api.siliconflow.cn/v1",
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.name = f"siliconflow:{model}"

    def rerank(self, query: str, documents: Sequence[str], top_n: Optional[int] = None) -> list[RerankResult]:
        if not documents:
            return []
        import urllib.request

        payload = json.dumps(
            {"model": self.model, "query": query, "documents": list(documents)}
        ).encode("utf-8")
        request = urllib.request.Request(
            f"{self.base_url}/rerank",
            data=payload,
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.loads(response.read().decode("utf-8"))

        items = data.get("results") or data.get("data") or []
        results: list[RerankResult] = []
        for position, item in enumerate(items):
            index = int(item.get("index", position))
            # 兼容 relevance_score / score 两种字段名
            raw_score = item.get("relevance_score", item.get("score", 0.0))
            results.append(RerankResult(index=index, score=float(raw_score)))
        results.sort(key=lambda r: r.score, reverse=True)
        return results[: (top_n if top_n is not None else settings.reranker_top_n)]


class HttpReranker(BaseReranker):
    """调用独立重排服务（POST {url} {"query":..., "documents":[...]})。"""

    def __init__(self, url: str, timeout: float = 30.0) -> None:
        self.url = url
        self.timeout = timeout
        self.name = f"http:{url}"

    def rerank(self, query: str, documents: Sequence[str], top_n: Optional[int] = None) -> list[RerankResult]:
        payload = json.dumps({"query": query, "documents": list(documents)}).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        items = data.get("results") or data.get("data") or []
        results = [RerankResult(index=int(it["index"]), score=float(it["score"])) for it in items]
        results.sort(key=lambda r: r.score, reverse=True)
        return results[: (top_n if top_n is not None else settings.reranker_top_n)]


_reranker: Optional[BaseReranker] = None


def _build_reranker() -> BaseReranker:
    provider = (settings.reranker_provider or "local").lower()
    if provider == "score_fusion":
        return ScoreFusionReranker()
    if provider == "siliconflow":
        if not settings.reranker_api_key:
            logger.warning("未配置 SUPERVISION_RERANKER_API_KEY，Reranker 降级为分数融合")
            return ScoreFusionReranker()
        logger.info("Reranker 使用硅基流动托管服务", extra={"model": settings.reranker_model})
        return SiliconFlowReranker(
            settings.reranker_model, settings.reranker_api_key, settings.reranker_api_base_url
        )
    if provider == "http":
        return HttpReranker(settings.reranker_http_url)
    from pathlib import Path

    model_dir = Path(settings.faiss_index_dir).resolve().parents[1] / "models" / "bge-reranker-v2-m3"
    # 同 Embedding：无本地权重时快速降级，避免访问被阻断的模型仓库导致挂起
    if not (model_dir.is_dir() and (model_dir / "config.json").exists()):
        logger.warning(
            "本地未找到 Reranker 模型权重，降级为分数融合重排（避免访问外网挂起）",
            extra={
                "expected_dir": str(model_dir),
                "hint": "把 BGE-Reranker 放到该目录，或设置 SUPERVISION_RERANKER_PROVIDER=http",
                "degraded": True,
            },
        )
        return ScoreFusionReranker()
    try:
        reranker = LocalReranker(str(model_dir), settings.reranker_device)
        reranker.rerank("探测", ["探测文档"])
        return reranker
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Reranker 模型加载失败，降级为分数融合重排",
            extra={"error": str(exc)[:300], "degraded": True},
        )
        return ScoreFusionReranker()


def get_reranker() -> BaseReranker:
    global _reranker
    if _reranker is None:
        if not settings.rerank_enabled:
            _reranker = ScoreFusionReranker()
        else:
            _reranker = _build_reranker()
        logger.info("Reranker 就绪", extra={"reranker": _reranker.name, "degraded": _reranker.degraded})
    return _reranker


def set_reranker(reranker: Optional[BaseReranker]) -> None:
    global _reranker
    _reranker = reranker


async def rerank_documents(
    query: str, documents: Sequence[str], top_n: Optional[int] = None
) -> list[RerankResult]:
    import asyncio

    reranker = get_reranker()
    return await asyncio.to_thread(reranker.rerank, query, list(documents), top_n)


__all__ = [
    "BaseReranker",
    "ScoreFusionReranker",
    "LocalReranker",
    "HttpReranker",
    "SiliconFlowReranker",
    "get_reranker",
    "set_reranker",
    "rerank_documents",
]
