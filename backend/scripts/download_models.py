# -*- coding: utf-8 -*-
"""下载 BGE 模型到本地缓存（embedding / reranker）。

本机直连 huggingface.co 会被重置，但存在本地代理（默认 127.0.0.1:7897），
脚本会按顺序尝试：HF_ENDPOINT 镜像 → 本地代理 → 直连。
"""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path

CACHE_ROOT = Path(r"D:\Docment\supervision-qc\var\models")
TARGETS = [
    ("BAAI/bge-m3", "bge-m3"),
    ("BAAI/bge-reranker-v2-m3", "bge-reranker-v2-m3"),
]


def try_download(repo_id: str, local_dir: Path, endpoint: str | None, proxy: str | None) -> bool:
    if endpoint:
        os.environ["HF_ENDPOINT"] = endpoint
    else:
        os.environ.pop("HF_ENDPOINT", None)
    for key in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
        if proxy:
            os.environ[key] = proxy
        else:
            os.environ.pop(key, None)
    os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

    from huggingface_hub import snapshot_download

    label = endpoint or proxy or "direct"
    started = time.time()
    print(f"[{repo_id}] try {label} ...", flush=True)
    try:
        path = snapshot_download(
            repo_id=repo_id,
            local_dir=str(local_dir),
            resume_download=True,
            max_workers=4,
            ignore_patterns=["*.msgpack", "*.h5", "*.onnx", "onnx/*", "*.tflite", "*.ot"],
        )
        print(f"[{repo_id}] OK -> {path} ({time.time() - started:.0f}s)", flush=True)
        return True
    except Exception as exc:  # noqa: BLE001
        print(f"[{repo_id}] FAIL {label}: {type(exc).__name__}: {str(exc)[:200]}", flush=True)
        return False


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    attempts = [
        ("https://hf-mirror.com", None),
        (None, "http://127.0.0.1:7897"),
        (None, None),
    ]
    results: dict[str, bool] = {}
    for repo_id, folder in TARGETS:
        if only and only not in repo_id:
            continue
        local_dir = CACHE_ROOT / folder
        local_dir.mkdir(parents=True, exist_ok=True)
        if (local_dir / "config.json").exists():
            print(f"[{repo_id}] 已存在，跳过", flush=True)
            results[repo_id] = True
            continue
        for endpoint, proxy in attempts:
            if try_download(repo_id, local_dir, endpoint, proxy):
                results[repo_id] = True
                break
        else:
            results[repo_id] = False
    print("SUMMARY", results, flush=True)
    return 0 if all(results.values()) else 2


if __name__ == "__main__":
    raise SystemExit(main())
