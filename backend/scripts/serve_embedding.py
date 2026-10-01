# -*- coding: utf-8 -*-
"""Embedding 推理服务（SRS 7.7 Compose 中的 embedding 容器）。

与 app/retrieval/embedding.py 的 HttpEmbedder 协议一致：
    POST /embed  {"texts": [...], "is_query": bool}
    -> {"vectors": [[...]], "model": "...", "dim": 1024}

用法：
    python scripts/serve_embedding.py --port 8510 --model BAAI/bge-m3
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path
from typing import Any, Optional

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from fastapi import FastAPI  # noqa: E402
from pydantic import BaseModel  # noqa: E402

app = FastAPI(title="Embedding Service", version="1.0.0")

_state: dict[str, Any] = {"model": None, "name": "", "dim": 0, "device": "cpu", "load_error": None}


class EmbedRequest(BaseModel):
    texts: list[str]
    is_query: bool = False


def _load_model(model_name: str, device: str) -> None:
    from sentence_transformers import SentenceTransformer

    started = time.perf_counter()
    model = SentenceTransformer(
        model_name,
        device=device,
        cache_folder=os.environ.get("SUPERVISION_EMBEDDING_CACHE_DIR") or None,
        trust_remote_code=True,
    )
    _state.update(
        model=model,
        name=model_name,
        dim=int(model.get_sentence_embedding_dimension()),
        device=device,
        load_error=None,
    )
    print(f"[embedding] loaded {model_name} dim={_state['dim']} in {time.perf_counter() - started:.1f}s", flush=True)


@app.on_event("startup")
def startup() -> None:  # pragma: no cover - 依赖真实模型
    model_name = os.environ.get("SUPERVISION_EMBEDDING_MODEL", "BAAI/bge-m3")
    device = os.environ.get("SUPERVISION_EMBEDDING_DEVICE", "cpu")
    try:
        _load_model(model_name, device)
    except Exception as exc:  # noqa: BLE001
        _state["load_error"] = f"{type(exc).__name__}: {exc}"
        print(f"[embedding] load failed: {_state['load_error']}", flush=True)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok" if _state["model"] is not None else "degraded",
        "model": _state["name"],
        "dim": _state["dim"],
        "error": _state["load_error"],
    }


@app.post("/embed")
def embed(request: EmbedRequest) -> dict:
    if _state["model"] is None:
        return {"vectors": [], "model": _state["name"], "error": _state["load_error"]}
    texts = [t if not request.is_query else f"为这个句子生成表示以用于检索相关文章：{t}" for t in request.texts]
    vectors = _state["model"].encode(
        texts,
        batch_size=int(os.environ.get("SUPERVISION_EMBEDDING_BATCH_SIZE", "16")),
        normalize_embeddings=True,
        show_progress_bar=False,
    )
    return {"vectors": [v.tolist() for v in vectors], "model": _state["name"], "dim": _state["dim"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Embedding 推理服务")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8510)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
