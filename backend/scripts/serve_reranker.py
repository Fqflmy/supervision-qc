# -*- coding: utf-8 -*-
"""Reranker 推理服务（SRS 7.7 Compose 中的 reranker 容器）。

与 app/retrieval/reranker.py 的 HttpReranker 协议一致：
    POST /rerank  {"query": "...", "documents": [...]}
    -> {"results": [{"index": 0, "score": 0.93}, ...]}（按 score 降序）

用法：
    python scripts/serve_reranker.py --port 8520 --model BAAI/bge-reranker-v2-m3
"""
from __future__ import annotations

import argparse
import math
import os
import sys
import time
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from fastapi import FastAPI  # noqa: E402
from pydantic import BaseModel  # noqa: E402

app = FastAPI(title="Reranker Service", version="1.0.0")

_state: dict[str, Any] = {"model": None, "name": "", "device": "cpu", "load_error": None}


class RerankRequest(BaseModel):
    query: str
    documents: list[str]
    top_n: int | None = None


def _load_model(model_name: str, device: str) -> None:
    from sentence_transformers import CrossEncoder

    started = time.perf_counter()
    model = CrossEncoder(model_name, device=device, max_length=512)
    _state.update(model=model, name=model_name, device=device, load_error=None)
    print(f"[reranker] loaded {model_name} in {time.perf_counter() - started:.1f}s", flush=True)


@app.on_event("startup")
def startup() -> None:  # pragma: no cover - 依赖真实模型
    model_name = os.environ.get("SUPERVISION_RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    device = os.environ.get("SUPERVISION_RERANKER_DEVICE", "cpu")
    try:
        _load_model(model_name, device)
    except Exception as exc:  # noqa: BLE001
        _state["load_error"] = f"{type(exc).__name__}: {exc}"
        print(f"[reranker] load failed: {_state['load_error']}", flush=True)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok" if _state["model"] is not None else "degraded",
        "model": _state["name"],
        "error": _state["load_error"],
    }


@app.post("/rerank")
def rerank(request: RerankRequest) -> dict:
    if _state["model"] is None or not request.documents:
        return {"results": [], "error": _state["load_error"]}

    pairs = [(request.query, doc) for doc in request.documents]
    raw_scores = _state["model"].predict(
        pairs,
        batch_size=int(os.environ.get("SUPERVISION_RERANKER_BATCH_SIZE", "16")),
        show_progress_bar=False,
    )
    # sigmoid 归一化到 0-1，与 ScoreFusionReranker 保持同一量纲
    results = [
        {"index": index, "score": 1 / (1 + math.exp(-float(score)))}
        for index, score in enumerate(raw_scores)
    ]
    results.sort(key=lambda item: item["score"], reverse=True)
    if request.top_n:
        results = results[: request.top_n]
    return {"results": results, "model": _state["name"]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Reranker 推理服务")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8520)
    args = parser.parse_args()

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
