# -*- coding: utf-8 -*-
"""应用入口（SRS 6.1：/api/v1 前缀、统一响应体、CORS、trace_id）。"""
from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.routes import admin, auth, eval, judge, knowledge, retrieval, users
from app.config import settings
from app.constants import ERROR_CODES
from app.core.errors import AppError
from app.core.logging_conf import get_logger, set_trace_id, setup_logging
from app.core.response import fail

setup_logging(settings.log_level, json_output=settings.log_json)
logger = get_logger(__name__)

# Windows 下必须在事件循环创建前切换为 SelectorEventLoop，
# 否则 psycopg 异步连接（LangGraph PostgreSQL 检查点）无法工作。
# 详见 app/agent/runner.py:_ensure_selector_event_loop_policy
if sys.platform == "win32":  # pragma: no cover - 平台相关
    with contextlib.suppress(Exception):
        if type(asyncio.get_event_loop_policy()) is not asyncio.WindowsSelectorEventLoopPolicy:
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """启动自检：数据库、图谱、模型与索引状态以日志形式显式暴露，避免静默降级。"""
    from app.core.security_policy import assert_production_safe, auth_security_warnings
    from app.db.session import init_db, ping

    # 安全前置校验：生产环境若使用占位/过短/自动生成的 JWT 密钥，直接拒绝启动。
    # 该密钥泄漏会导致任何人都能伪造令牌绕过鉴权，因此不允许带病上线。
    assert_production_safe(settings)
    for tip in auth_security_warnings(settings):
        logger.warning("安全提示", extra={"item": tip, "environment": settings.environment})

    started = time.perf_counter()
    db_ok = init_db()
    logger.info(
        "服务启动",
        extra={
            "app": settings.app_name,
            "version": settings.app_version,
            "environment": settings.environment,
            "database_ok": db_ok,
            "database_kind": "sqlite" if settings.is_sqlite else "postgresql",
        },
    )
    if not db_ok:
        logger.error("数据库不可用，请检查 database_url 或先启动数据层：deploy/docker-compose-infra.yml")

    try:
        from app.kg.graph_store import get_graph_store

        store = get_graph_store()
        available = store.available
        logger.info("图谱状态", extra={"neo4j_available": available, "uri": settings.neo4j_uri})
        if available:
            store.init_schema()
    except Exception as exc:  # noqa: BLE001
        logger.warning("图谱初始化异常", extra={"error": str(exc)[:200]})

    try:
        from app.retrieval.embedding import get_embedder
        from app.retrieval.reranker import get_reranker

        embedder = get_embedder()
        reranker = get_reranker()
        logger.info(
            "检索模型状态",
            extra={
                "embedder": embedder.name,
                "embedder_degraded": embedder.degraded,
                "reranker": reranker.name,
                "reranker_degraded": reranker.degraded,
            },
        )
        # 预热：首次真实调用会把模型加载/连接建立成本前移到启动期，
        # 否则第一个用户请求要额外等数秒到数十秒（外部 Embedding 服务尤其明显）。
        if not embedder.degraded:
            warm_started = time.perf_counter()
            try:
                embedder.encode_one("预热", is_query=True)
                logger.info(
                    "Embedding 预热完成",
                    extra={"elapsed_ms": int((time.perf_counter() - warm_started) * 1000)},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("Embedding 预热失败（不影响启动）", extra={"error": str(exc)[:200]})
    except Exception as exc:  # noqa: BLE001
        logger.warning("检索模型初始化异常", extra={"error": str(exc)[:200]})

    if settings.llm_provider != "fake" and not settings.llm_primary_api_key:
        logger.warning(
            "未配置主模型 API Key，LLM 相关能力将失败",
            extra={"hint": "设置环境变量 SUPERVISION_LLM_PRIMARY_API_KEY"},
        )

    logger.info("启动完成", extra={"elapsed_ms": int((time.perf_counter() - started) * 1000)})
    try:
        yield
    finally:
        from app.db.session import dispose_engine

        dispose_engine()
        logger.info("服务已停止")


app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description=(
        "工程监理质量智能评估系统后端 API。\n\n"
        "- 知识库与图谱：`/api/v1/kb/*`\n"
        "- 检索与问答：`/api/v1/retrieval/*`\n"
        "- 评估 Agent：`/api/v1/eval/*`\n"
        "- 质量评审：`/api/v1/judge/*`\n"
        "- 系统与审计：`/api/v1/*`"
    ),
    openapi_url=f"{settings.api_prefix}/openapi.json",
    docs_url=f"{settings.api_prefix}/docs",
    redoc_url=f"{settings.api_prefix}/redoc",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["x-trace-id"],
)


@app.middleware("http")
async def trace_middleware(request: Request, call_next):
    """trace_id 贯穿全链路，并记录访问日志（SRS 7.4）。"""
    trace_id = set_trace_id(request.headers.get("x-trace-id"))
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        elapsed = int((time.perf_counter() - started) * 1000)
        logger.exception(
            "请求处理异常",
            extra={"method": request.method, "path": request.url.path, "elapsed_ms": elapsed},
        )
        raise
    elapsed = int((time.perf_counter() - started) * 1000)
    response.headers["x-trace-id"] = trace_id
    # 健康检查与指标端点会被容器/负载均衡高频轮询（默认每 15-20 秒一次），
    # 全量记录会淹没真正的业务日志、增加排查噪声；仅在异常状态码时记录。
    quiet = request.url.path in {"/metrics"} or (
        request.url.path.endswith("/health") and response.status_code < 400
    )
    if not quiet:
        logger.info(
            "请求完成",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "elapsed_ms": elapsed,
            },
        )
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    logger.warning(
        "业务异常",
        extra={
            "code": exc.code,
            "reason": exc.message,
            "path": request.url.path,
            "details": exc.details,
        },
    )
    return JSONResponse(status_code=exc.http_status, content=fail(exc.code, exc.message, exc.details))


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
    errors = [
        {"field": ".".join(str(p) for p in err.get("loc", [])), "message": err.get("msg")}
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=fail(ERROR_CODES["PARAM_INVALID"], "参数校验失败", errors),
    )


@app.exception_handler(Exception)
async def unhandled_error_handler(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("未处理异常", extra={"path": request.url.path})
    return JSONResponse(
        status_code=500,
        content=fail(ERROR_CODES["INTERNAL_ERROR"], "内部服务异常", {"error": str(exc)[:300]}),
    )


# --------------------------------------------------------------------------- #
# 路由注册（SRS 6.2）
# --------------------------------------------------------------------------- #
for router_module in (auth, knowledge, retrieval, eval, judge, admin, users):
    app.include_router(router_module.router, prefix=settings.api_prefix)


@app.get("/", include_in_schema=False)
def root() -> dict:
    return {
        "app": settings.app_name,
        "version": settings.app_version,
        "docs": f"{settings.api_prefix}/docs",
        "health": f"{settings.api_prefix}/health",
    }


def run() -> None:  # pragma: no cover - 手动启动入口
    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        reload=settings.debug,
        log_config=None,
    )


if __name__ == "__main__":  # pragma: no cover
    run()
