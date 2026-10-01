# -*- coding: utf-8 -*-
"""Embedding 适配层（SRS 2.3 / 5.3）。

支持三类 provider：
- ``local``  : sentence-transformers / FlagEmbedding 加载 BGE 模型（GPU/CPU）；
- ``http``   : 调用独立 Embedding 推理服务（对应 Compose 中的 embedding 容器）；
- ``hash``   : 确定性哈希向量，完全离线，用于单元测试与无模型环境的降级。

自动降级策略：``local`` 模型不可用时回退 ``hash``，并在 ``Embedder.name`` 中标注，
检索结果会带上 ``degraded`` 标记，保证「不静默失真」。
"""
from __future__ import annotations

import hashlib
import json
import math
import threading
import urllib.request
from typing import Optional, Sequence

from app.config import settings
from app.core.logging_conf import get_logger
from app.llm.base import EmbeddingResult

logger = get_logger(__name__)


class BaseEmbedder:
    name: str = "base"
    dim: int = settings.embedding_dim
    degraded: bool = False

    def encode(self, texts: Sequence[str], *, is_query: bool = False) -> list[list[float]]:
        raise NotImplementedError

    def encode_one(self, text: str, *, is_query: bool = False) -> list[float]:
        return self.encode([text], is_query=is_query)[0]

    def encode_result(self, texts: Sequence[str], *, is_query: bool = False) -> EmbeddingResult:
        vectors = self.encode(texts, is_query=is_query)
        return EmbeddingResult(vectors=vectors, model=self.name, dim=self.dim)


class HashEmbedder(BaseEmbedder):
    """确定性哈希向量：字符 n-gram + 中文分词哈希投影，L2 归一化。

    不是语义模型，但能捕捉词面重叠，足以驱动 BM25/向量/RRF/重排全链路与离线测试。
    """

    name = "hash-ngram-1024"
    degraded = True

    def __init__(self, dim: Optional[int] = None) -> None:
        self.dim = dim or settings.embedding_dim
        self._jieba = None
        self._lock = threading.Lock()

    def _tokens(self, text: str) -> list[str]:
        text = (text or "").strip()
        if not text:
            return []
        tokens: list[str] = []
        with self._lock:
            if self._jieba is None:
                try:
                    import jieba

                    jieba.setLogLevel(60)
                    self._jieba = jieba
                except Exception:  # noqa: BLE001
                    self._jieba = False
            jieba_mod = self._jieba
        if jieba_mod:
            tokens.extend(t for t in jieba_mod.lcut(text) if t.strip())
        else:  # pragma: no cover
            tokens.extend(text.split())
        chars = "".join(ch for ch in text if not ch.isspace())
        tokens.extend(chars[i : i + 2] for i in range(max(0, len(chars) - 1)))
        tokens.extend(chars[i : i + 3] for i in range(max(0, len(chars) - 2)))
        return tokens

    @staticmethod
    def _bucket(token: str, dim: int) -> tuple[int, float]:
        digest = hashlib.md5(token.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        return index, sign

    def encode(self, texts: Sequence[str], *, is_query: bool = False) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            vec = [0.0] * self.dim
            for token in self._tokens(text):
                index, sign = self._bucket(token, self.dim)
                vec[index] += sign * (1.0 + 0.5 * min(len(token), 6))
            norm = math.sqrt(sum(v * v for v in vec))
            if norm > 0:
                vec = [v / norm for v in vec]
            vectors.append(vec)
        return vectors


class LocalEmbedder(BaseEmbedder):
    """sentence-transformers 加载本地 BGE 模型。"""

    def __init__(self, model_path: str, device: str = "cpu") -> None:
        self.model_path = model_path
        self.device = device
        self.name = f"local:{model_path.split('/')[-1].split(chr(92))[-1]}"
        self._model = None
        self._lock = threading.Lock()

    def _load(self):
        with self._lock:
            if self._model is None:
                from sentence_transformers import SentenceTransformer

                logger.info("加载 Embedding 模型", extra={"path": self.model_path, "device": self.device})
                self._model = SentenceTransformer(
                    self.model_path,
                    device=self.device,
                    cache_folder=settings.embedding_cache_dir,
                    trust_remote_code=True,
                )
                self.dim = int(self._model.get_sentence_embedding_dimension())
            return self._model

    def encode(self, texts: Sequence[str], *, is_query: bool = False) -> list[list[float]]:
        model = self._load()
        # BGE 系列检索任务建议给 query 加指令前缀
        prepared = [f"为这个句子生成表示以用于检索相关文章：{t}" if is_query else t for t in texts]
        vectors = model.encode(
            prepared,
            batch_size=settings.embedding_batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return [v.tolist() for v in vectors]


class SiliconFlowEmbedder(BaseEmbedder):
    """硅基流动（SiliconFlow）托管的 Embedding 服务，走 OpenAI 兼容协议。

    适合无法下载本地权重、又没有自建推理服务的场景：
    无需 torch/sentence-transformers，启动快，且向量质量与本地 BGE 一致。
    """

    def __init__(
        self,
        model: str,
        api_key: str,
        base_url: str = "https://api.siliconflow.cn/v1",
        dim: Optional[int] = None,
        timeout: float = 60.0,
    ) -> None:
        self.model = model
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.dim = dim or settings.embedding_dim
        self.timeout = timeout
        self.name = f"siliconflow:{model}"
        self._client = None

    def _get_client(self):
        if self._client is None:
            from openai import OpenAI

            self._client = OpenAI(base_url=self.base_url, api_key=self.api_key, timeout=self.timeout, max_retries=1)
        return self._client

    def encode(self, texts: Sequence[str], *, is_query: bool = False) -> list[list[float]]:
        client = self._get_client()
        response = client.embeddings.create(model=self.model, input=list(texts))
        vectors = [item.embedding for item in response.data]
        if vectors:
            self.dim = len(vectors[0])
        return vectors


class HttpEmbedder(BaseEmbedder):
    """调用独立 Embedding 服务（POST {url} {"texts": [...], "is_query": bool}）。"""

    def __init__(self, url: str, dim: Optional[int] = None, timeout: float = 30.0) -> None:
        self.url = url
        self.dim = dim or settings.embedding_dim
        self.timeout = timeout
        self.name = f"http:{url}"

    def encode(self, texts: Sequence[str], *, is_query: bool = False) -> list[list[float]]:
        payload = json.dumps({"texts": list(texts), "is_query": is_query}).encode("utf-8")
        request = urllib.request.Request(
            self.url, data=payload, headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        vectors = data.get("vectors") or data.get("embeddings") or data.get("data")
        if not vectors:
            raise RuntimeError(f"Embedding 服务返回格式异常：{str(data)[:200]}")
        self.dim = len(vectors[0])
        return vectors


_embedder: Optional[BaseEmbedder] = None


def _local_model_ready(model_dir, marker: str = "config.json") -> bool:
    """本地模型目录是否就绪（避免在无权重时访问外网导致长时间挂起）。"""
    from pathlib import Path

    path = Path(model_dir)
    return path.is_dir() and (path / marker).exists()


def _build_embedder() -> BaseEmbedder:
    provider = (settings.embedding_provider or "local").lower()
    if provider == "hash":
        return HashEmbedder()
    if provider == "siliconflow":
        if not settings.embedding_api_key:
            logger.warning("未配置 SUPERVISION_EMBEDDING_API_KEY，Embedding 降级为哈希向量")
            return HashEmbedder()
        logger.info("Embedding 使用硅基流动托管服务", extra={"model": settings.embedding_model})
        return SiliconFlowEmbedder(
            settings.embedding_model, settings.embedding_api_key, settings.embedding_api_base_url
        )
    if provider == "http":
        return HttpEmbedder(settings.embedding_http_url, settings.embedding_dim)

    model_path = settings.embedding_model
    local_dir = _default_model_dir("bge-m3")
    # 只认本地目录：内网/离线环境下 HF 不可达时，直接加载会挂起数十秒甚至数分钟。
    # 若目录没有权重，快速降级为哈希向量并明确提示放置位置。
    if not _local_model_ready(local_dir):
        logger.warning(
            "本地未找到 Embedding 模型权重，降级为哈希向量（避免访问外网挂起）",
            extra={
                "expected_dir": str(local_dir),
                "hint": "把 BGE 模型放到该目录，或设置 SUPERVISION_EMBEDDING_PROVIDER=http 指向推理服务",
                "degraded": True,
            },
        )
        return HashEmbedder()
    try:
        embedder = LocalEmbedder(str(local_dir), settings.embedding_device)
        embedder.encode(["连通性探测"], is_query=False)
        return embedder
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "Embedding 模型加载失败，降级为哈希向量",
            extra={"model": model_path, "path": str(local_dir), "error": str(exc)[:300], "degraded": True},
        )
        return HashEmbedder()


def _default_model_dir(folder: str):
    from pathlib import Path

    return Path(settings.faiss_index_dir).resolve().parents[1] / "models" / folder


def get_embedder() -> BaseEmbedder:
    global _embedder
    if _embedder is None:
        _embedder = _build_embedder()
        logger.info(
            "Embedding 就绪",
            extra={"embedder": _embedder.name, "dim": _embedder.dim, "degraded": _embedder.degraded},
        )
    return _embedder


def set_embedder(embedder: Optional[BaseEmbedder]) -> None:
    global _embedder
    _embedder = embedder


async def embed_texts(texts: Sequence[str], *, is_query: bool = False) -> list[list[float]]:
    """异步包装：模型推理是 CPU 阻塞操作，放到线程池避免阻塞事件循环。"""
    import asyncio

    embedder = get_embedder()
    return await asyncio.to_thread(embedder.encode, list(texts), is_query=is_query)


__all__ = [
    "BaseEmbedder",
    "HashEmbedder",
    "LocalEmbedder",
    "HttpEmbedder",
    "SiliconFlowEmbedder",
    "get_embedder",
    "set_embedder",
    "embed_texts",
]
