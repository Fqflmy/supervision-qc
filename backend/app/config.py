# -*- coding: utf-8 -*-
"""全局配置。环境变量前缀 SUPERVISION_，支持 .env 文件。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

#: 后端根目录（backend/）。app/config.py 位于 backend/app/config.py，
#: 因此 parents[0]=app、parents[1]=backend、parents[2]=仓库根。
#: .env 与 var/ 都相对该目录；若写成 parents[2]，backend/.env 会被静默忽略，
#: 所有配置回退到默认值（表现为「填了 Key 也不生效」）。
PROJECT_ROOT = Path(__file__).resolve().parents[1]


def split_csv_list(value) -> List[str]:
    """把「逗号分隔字符串」或 JSON 数组解析为字符串列表。

    兼容三种写法，避免因写法差异导致启动失败：
        a,b            -> ["a", "b"]
        ["a","b"]      -> ["a", "b"]
        （空/None）     -> []
    """
    if value is None:
        return []
    if isinstance(value, (list, tuple, set)):
        return [str(item).strip() for item in value if str(item).strip()]
    raw = str(value).strip()
    if not raw:
        return []
    if raw.startswith("[") and raw.endswith("]"):
        try:
            import json

            parsed = json.loads(raw)
            if isinstance(parsed, list):
                return [str(item).strip() for item in parsed if str(item).strip()]
        except Exception:  # noqa: BLE001 - 解析失败则退回逗号切分
            pass
    return [item.strip() for item in raw.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        env_prefix="SUPERVISION_",
        extra="ignore",
        case_sensitive=False,
    )

    # ---------- 基础 ----------
    app_name: str = "工程监理质量智能评估系统"
    app_version: str = "1.0.0"
    environment: str = "dev"
    debug: bool = True
    api_prefix: str = "/api/v1"
    host: str = "0.0.0.0"
    port: int = 8000
    log_level: str = "INFO"
    log_json: bool = True

    # ---------- 数据库（SRS 5.2 PostgreSQL 15+）----------
    database_url: str = (
        "postgresql+psycopg://supervision:supervision@127.0.0.1:5432/supervision"
    )
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_echo: bool = False
    db_connect_timeout: int = 3

    # ---------- Neo4j（SRS 5.4）----------
    neo4j_uri: str = "bolt://127.0.0.1:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "supervision123"
    neo4j_database: str = "neo4j"
    neo4j_enabled: bool = True
    neo4j_max_conn_lifetime: int = 3600
    neo4j_ref_max_depth: int = 3

    # ---------- Redis ----------
    redis_url: str = "redis://127.0.0.1:6379/0"
    redis_enabled: bool = True
    task_queue_key: str = "supervision:eval:queue"
    cache_ttl_seconds: int = 3600

    # ---------- 对象存储（原始文件）----------
    storage_backend: str = "local"  # local | minio
    #: 留空则自动派生为 {data_dir}/storage；容器部署只改 data_dir 一处即可
    storage_local_dir: str = ""
    minio_endpoint: str = "127.0.0.1:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "supervision"
    minio_secure: bool = False

    # ---------- LLM 网关（SRS 2.3）----------
    llm_provider: str = "openai_compatible"  # openai_compatible | fake
    llm_primary_model: str = "deepseek-chat"
    llm_primary_base_url: str = "https://api.deepseek.com/v1"
    llm_primary_api_key: str = ""
    llm_fallback_model: str = "qwen-plus"
    llm_fallback_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    llm_fallback_api_key: str = ""
    llm_temperature: float = 0.1
    llm_max_tokens: int = 4096
    llm_timeout_seconds: float = 60.0
    llm_max_retries: int = 3
    llm_retry_backoff_seconds: float = 1.0
    llm_concurrency: int = 8
    llm_enable_fallback: bool = True

    # ---------- Embedding（BGE-M3）----------
    # provider: local=本地权重 | http=自建推理服务 | siliconflow=硅基流动托管 | hash=无模型降级
    embedding_provider: str = "local"
    embedding_model: str = "BAAI/bge-m3"
    embedding_dim: int = 1024
    embedding_device: str = "cpu"
    embedding_batch_size: int = 16
    embedding_max_length: int = 1024
    embedding_http_url: str = "http://127.0.0.1:8510/embed"
    embedding_api_key: str = ""
    embedding_api_base_url: str = "https://api.siliconflow.cn/v1"
    embedding_cache_dir: Optional[str] = None
    embedding_offline: bool = False

    # ---------- Reranker（BGE-Reranker）----------
    # provider: local | http | siliconflow | score_fusion
    reranker_provider: str = "local"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    reranker_device: str = "cpu"
    reranker_batch_size: int = 16
    reranker_http_url: str = "http://127.0.0.1:8520/rerank"
    reranker_api_key: str = ""
    reranker_api_base_url: str = "https://api.siliconflow.cn/v1"
    reranker_top_n: int = 8

    # ---------- 向量库 FAISS（SRS 5.3）----------
    #: 留空则自动派生为 {data_dir}/faiss；容器部署只改 data_dir 一处即可
    faiss_index_dir: str = ""
    faiss_index_type: str = "flat"  # flat | ivf
    faiss_ivf_nlist: int = 4096
    faiss_ivf_nprobe: int = 32
    faiss_flat_threshold: int = 500_000

    # ---------- 检索 Pipeline（SRS 12.1）----------
    chunk_size: int = 512
    chunk_overlap: int = 64
    chunk_min_size: int = 80
    bm25_top_k: int = 50
    dense_top_k: int = 50
    multi_query_enabled: bool = True
    multi_query_n: int = 4
    rrf_k: int = 60
    bm25_weight: float = 0.4
    dense_weight: float = 0.6
    fusion_top_k: int = 20
    no_evidence_threshold: float = 0.35
    kg_expand_enabled: bool = True
    kg_expand_limit: int = 5
    rerank_enabled: bool = True
    context_token_budget: int = 6000

    # ---------- Agent 状态机与防死循环（SRS 4.3）----------
    agent_max_iterations: int = 12
    agent_no_progress_limit: int = 2
    agent_tool_timeout_seconds: float = 30.0
    agent_step_max_retries: int = 3
    agent_task_timeout_seconds: float = 900.0
    agent_token_budget: int = 200_000
    agent_max_concurrency: int = 10
    checkpoint_backend: str = "auto"  # auto | memory | postgres
    checkpoint_table: str = "langgraph_checkpoints"

    # ---------- Judge（SRS 3.5）----------
    judge_enabled: bool = True
    judge_threshold: float = 3.5
    judge_conflict_delta: float = 1.0
    judge_citation_check_enabled: bool = True
    #: 引用校验折合的扣分（按比例，避免 1 条错误把维度直接打到 0）
    judge_penalty_hallucination: float = 3.0
    judge_penalty_abolished: float = 0.5
    judge_penalty_inconsistent: float = 0.25
    judge_penalty_max: float = 3.0
    #: 逗号分隔字符串；用 settings.judge_dimension_list 取列表
    judge_dimensions: str = (
        "clause_citation_accuracy,conclusion_reasonableness,evidence_sufficiency,"
        "format_compliance,remediation_actionability"
    )

    # ---------- 安全 ----------
    jwt_secret: str = "change-me-in-production-please-32bytes-min"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 120
    refresh_token_expire_days: int = 7
    password_min_length: int = 8
    login_max_failures: int = 5
    login_lock_minutes: int = 15
    #: 逗号分隔字符串；用 settings.cors_origin_list 取列表
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173,http://localhost:8080,http://127.0.0.1:8080"

    # ---------- 上传限制（SRS FR-KB-01）----------
    upload_max_file_mb: int = 200
    upload_max_batch: int = 50
    #: 逗号分隔字符串；用 settings.upload_allowed_ext_list 取列表
    upload_allowed_ext: str = ".pdf,.doc,.docx,.txt,.xlsx,.xls"

    # ---------- 解析 ----------
    parser_ocr_enabled: bool = False
    parser_min_text_chars_per_page: int = 30


    # ---------- 数据目录 ----------
    #: 运行期数据根目录。容器部署时必须把它指向挂载的卷（compose 里设为 /app/var），
    #: 否则索引/上传文件会写进镜像层：进程重启即丢失，且初始化容器与 api 容器
    #: 会各自写各自的副本，表现为「数据明明写入了却检索不到」。
    data_dir: str = str(PROJECT_ROOT / "var")

    @property
    def var_dir(self) -> Path:
        return Path(self.data_dir)

    @property
    def resolved_storage_dir(self) -> str:
        """实际使用的对象存储目录（未显式配置时跟随 data_dir）。"""
        return self.storage_local_dir or str(self.var_dir / "storage")

    @property
    def resolved_faiss_dir(self) -> str:
        """实际使用的 FAISS 索引目录（未显式配置时跟随 data_dir）。"""
        return self.faiss_index_dir or str(self.var_dir / "faiss")

    @property
    def is_sqlite(self) -> bool:
        return self.database_url.startswith("sqlite")

    def ensure_dirs(self) -> None:
        self.resolve_paths()
        for path in (
            self.storage_local_dir,
            self.faiss_index_dir,
            self.data_dir,
        ):
            Path(path).mkdir(parents=True, exist_ok=True)
        if self.embedding_cache_dir:
            Path(self.embedding_cache_dir).mkdir(parents=True, exist_ok=True)

    def resolve_paths(self) -> None:
        """把未显式配置的路径派生自 data_dir。

        必须在任何读取 ``storage_local_dir`` / ``faiss_index_dir`` 的代码之前调用
        （模块导入期与 ``get_settings()`` 内都会调用），否则会读到空字符串，
        导致索引写到当前工作目录这种难以排查的位置。
        """
        if not self.storage_local_dir:
            self.storage_local_dir = str(self.var_dir / "storage")
        if not self.faiss_index_dir:
            self.faiss_index_dir = str(self.var_dir / "faiss")


    # ---------- 派生属性：把 CSV 字段暴露为列表 ---------- #
    @property
    def cors_origin_list(self) -> List[str]:
        return split_csv_list(self.cors_origins)

    @property
    def upload_allowed_ext_list(self) -> List[str]:
        return split_csv_list(self.upload_allowed_ext)

    @property
    def judge_dimension_list(self) -> List[str]:
        return split_csv_list(self.judge_dimensions)

    @property
    def sqlalchemy_url(self) -> str:
        return self.database_url


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.resolve_paths()
    settings.ensure_dirs()
    return settings


def reset_settings_cache() -> None:
    """测试用：清空配置缓存。"""
    get_settings.cache_clear()


settings = get_settings()

__all__ = ["Settings", "settings", "get_settings", "reset_settings_cache", "PROJECT_ROOT"]
