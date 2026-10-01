# -*- coding: utf-8 -*-
"""后端启动入口（推荐用这个而不是直接 `uvicorn app.main:app`）。

Windows 上 psycopg 异步连接要求 SelectorEventLoop，而 uvicorn 默认使用
ProactorEventLoop。事件循环策略必须在**循环创建之前**设置，因此不能依赖
`app.main` 导入时兜底（那时 uvicorn 已经建好循环），必须在启动入口先设置。

用法：
    python -m app.cli --port 8000
    python -m app.cli --port 8000 --workers 4 --reload
"""
from __future__ import annotations

import argparse
import asyncio
import sys

from app.config import settings


def _loop_factory(workers: int):
    """选择事件循环工厂。

    uvicorn ≥0.30 在 Windows 上硬编码使用 ProactorEventLoop（为支持子进程），
    而 psycopg 异步模式不支持 Proactor，会导致 LangGraph 的 PostgreSQL 检查点
    降级为内存检查点。这里显式传入 SelectorEventLoop 工厂绕开该默认值。

    多 worker 场景下 uvicorn 需要子进程监督，此时必须保留 Proactor。
    """
    if sys.platform != "win32":
        return None
    if workers > 1:
        return None  # 多进程必须用 Proactor
    return asyncio.SelectorEventLoop


def main() -> int:
    parser = argparse.ArgumentParser(description="启动后端 API 服务")
    parser.add_argument("--host", default=settings.host)
    parser.add_argument("--port", type=int, default=settings.port)
    parser.add_argument("--workers", type=int, default=1, help="FAISS/BM25 驻留内存，建议 1")
    parser.add_argument("--reload", action="store_true", help="开发热重载")
    parser.add_argument("--log-level", default=settings.log_level.lower())
    parser.add_argument("--access-log", action="store_true", help="打印访问日志")
    args = parser.parse_args()

    factory = _loop_factory(args.workers)
    loop_name = factory.__name__ if factory else "平台默认"

    import uvicorn

    print(
        f"[启动] {settings.app_name} v{settings.app_version} "
        f"http://{args.host}:{args.port} 环境={settings.environment} workers={args.workers} 事件循环={loop_name}",
        flush=True,
    )
    if settings.database_url.startswith("postgresql") and sys.platform == "win32" and factory is None:
        print(
            "[警告] 当前未使用 SelectorEventLoop，LangGraph 的 PostgreSQL 检查点会降级为内存检查点；"
            "如需持久化检查点请使用单 worker 启动",
            flush=True,
        )

    config_kwargs: dict = {
        "app": "app.main:app",
        "host": args.host,
        "port": args.port,
        "workers": args.workers,
        "reload": args.reload,
        "log_level": args.log_level,
        "access_log": args.access_log,
        "log_config": None,
    }
    # uvicorn 在 Windows 上通过 uvicorn.loops.asyncio.asyncio_loop_factory 选择事件循环，
    # 且硬编码返回 ProactorEventLoop（为支持子进程）。psycopg 异步不支持 Proactor，
    # 会导致 LangGraph 的 PostgreSQL 检查点降级为内存。单 worker 场景下这里替换工厂函数，
    # 让 uvicorn 使用 SelectorEventLoop；多 worker 必须保留 Proactor（需要子进程监督）。
    if factory is not None:
        from uvicorn.loops import asyncio as uvicorn_asyncio

        uvicorn_asyncio.asyncio_loop_factory = lambda use_subprocess=False: asyncio.SelectorEventLoop
        print("[提示] 已切换 uvicorn 事件循环为 SelectorEventLoop（PostgreSQL 检查点可用）", flush=True)

    server = uvicorn.Server(uvicorn.Config(**config_kwargs))
    server.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
