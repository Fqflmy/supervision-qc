# -*- coding: utf-8 -*-
"""pytest 公共夹具。

约定：
- 默认使用 Fake 大模型，离线可跑；
- 数据库相关测试在 PostgreSQL 不可用时自动 skip，而不是失败；
- 所有测试写入的索引落在 var/test-* 目录，避免污染开发数据。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

# Windows 下 psycopg 异步需要 SelectorEventLoop，必须在事件循环创建前设置
if sys.platform == "win32":  # pragma: no cover
    import asyncio

    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

# 必须在导入 app.* 之前设置环境
os.environ.setdefault("SUPERVISION_LLM_PROVIDER", "fake")
os.environ.setdefault("SUPERVISION_LOG_LEVEL", "ERROR")
os.environ.setdefault("SUPERVISION_LOG_JSON", "false")
os.environ.setdefault("SUPERVISION_NEO4J_ENABLED", "false")
os.environ.setdefault("SUPERVISION_EMBEDDING_PROVIDER", "hash")
os.environ.setdefault("SUPERVISION_RERANKER_PROVIDER", "score_fusion")


@pytest.fixture(scope="session")
def db_available() -> bool:
    from app.db.session import ping

    return ping()


@pytest.fixture()
def requires_db(db_available: bool):
    if not db_available:
        pytest.skip("PostgreSQL 不可用，跳过依赖数据库的用例（请先启动 deploy/docker-compose-infra.yml）")
    return True


@pytest.fixture()
def session(requires_db):
    from app.db.session import session_scope

    with session_scope() as s:
        yield s


@pytest.fixture(autouse=True)
def _clean_caches():
    """每个用例前后清理单例缓存，避免测试相互污染。"""
    from app.retrieval.bm25 import reset_bm25_indexes
    from app.retrieval.vector_store import reset_faiss_indexes

    reset_bm25_indexes()
    reset_faiss_indexes()
    yield
    reset_bm25_indexes()
    reset_faiss_indexes()


@pytest.fixture()
def fake_llm():
    from app.llm.gateway import FakeLlmGateway, set_llm

    gateway = FakeLlmGateway()
    set_llm(gateway)
    yield gateway
    set_llm(None)
