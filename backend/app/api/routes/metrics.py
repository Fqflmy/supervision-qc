# -*- coding: utf-8 -*-
"""Prometheus 指标端点（文本格式）。

为什么需要单独实现
------------------
1. **Prometheus 不支持 JSON**。原先 `prometheus.yml` 抓的是 `/api/v1/health`，
   返回的是统一响应包络 `{code, message, data, trace_id}`，即使改抓
   `/api/v1/metrics`（JSON）Prometheus 同样无法解析 —— 所以业务指标实际一条都没采到。
   本模块按 Prometheus 文本暴露格式（`text/plain; version=0.0.4`）输出。
2. **原 `/api/v1/metrics` 需要登录**。Prometheus 不会带 JWT，抓取必然 401。
   这里提供两种鉴权方式（见 ``authorize_scrape``），且生产环境必须至少启用一种。

为什么不引入 prometheus_client
------------------------------
文本暴露格式很小且稳定，手写可避免新增依赖（本项目 Docker 构建耗时敏感），
也让指标口径完全可读、可测。格式要点：HELP/TYPE 注释、`指标名{标签} 值`。
"""
from __future__ import annotations

from typing import Iterable, Optional, Sequence

from fastapi import APIRouter, Header, Request, Response
from sqlalchemy import func, select

from app.config import settings
from app.constants import ERROR_CODES
from app.core.logging_conf import get_logger

logger = get_logger(__name__)

router = APIRouter(tags=["可观测性"])

CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"


# --------------------------------------------------------------------------- #
# 文本格式渲染
# --------------------------------------------------------------------------- #
def _escape_label(value: str) -> str:
    """转义标签值：反斜杠、双引号、换行（Prometheus 文本格式要求）。"""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _fmt(value: float | int) -> str:
    """数值格式化：整数不带小数点，浮点保留有限精度。"""
    if isinstance(value, bool):
        return "1" if value else "0"
    if isinstance(value, int):
        return str(value)
    number = float(value)
    if number == int(number) and abs(number) < 1e15:
        return str(int(number))
    return f"{number:.4f}"


def render_metric(
    name: str,
    value: float | int,
    *,
    labels: Optional[dict[str, str]] = None,
    help_text: Optional[str] = None,
    metric_type: str = "gauge",
) -> list[str]:
    """渲染单个样本（含 HELP/TYPE 注释行）。"""
    lines: list[str] = []
    if help_text:
        lines.append(f"# HELP {name} {help_text}")
    lines.append(f"# TYPE {name} {metric_type}")
    if labels:
        rendered = ",".join(f'{k}="{_escape_label(str(v))}"' for k, v in sorted(labels.items()))
        lines.append(f"{name}{{{rendered}}} {_fmt(value)}")
    else:
        lines.append(f"{name} {_fmt(value)}")
    return lines


# --------------------------------------------------------------------------- #
# 鉴权
# --------------------------------------------------------------------------- #
def resolve_metrics_access() -> str:
    """解析指标抓取的放行方式，返回 ``bearer`` / ``public`` / ``denied``。

    与 ``resolve_schema_mode`` 同样的「默认安全 + 开发可用」思路：

    - 配置了令牌 → 只接受携带正确令牌的请求（推荐，生产必用）；
    - 未配置令牌 + 非生产环境 → 允许匿名抓取，保证 `docker compose up` 后
      Prometheus 立刻能采到数据，不需要先做一轮配置；
    - 未配置令牌 + 生产环境 → 拒绝，并提示配置方式。

    这样既不会「开箱即用却一条指标都采不到」，也不会「生产环境指标端点裸奔」。
    """
    if (settings.metrics_bearer_token or "").strip():
        return "bearer"
    if settings.metrics_public:
        return "public"
    is_production = (settings.environment or "dev").strip().lower() == "production"
    return "denied" if is_production else "public"


def authorize_scrape(authorization: Optional[str], client_host: Optional[str]) -> tuple[bool, str]:
    """判定本次抓取是否放行，返回 (是否放行, 原因)。"""
    mode = resolve_metrics_access()

    if mode == "bearer":
        token = (settings.metrics_bearer_token or "").strip()
        header = (authorization or "").strip()
        prefix = "bearer "
        if header.lower().startswith(prefix) and header[len(prefix):].strip() == token:
            return True, "bearer"
        return False, "令牌不匹配"

    if mode == "public":
        return True, "public"

    return False, "未配置抓取凭据（metrics_bearer_token / metrics_public）"


def metrics_config_warning() -> Optional[str]:
    """启动时提示指标抓取配置问题（None 表示无问题）。"""
    mode = resolve_metrics_access()
    if mode == "bearer":
        return None
    if mode == "public":
        if (settings.environment or "dev").strip().lower() == "production":
            return (
                "指标端点 /metrics 允许匿名抓取（metrics_public=true）。"
                "生产环境建议改用 SUPERVISION_METRICS_BEARER_TOKEN。"
            )
        return None
    return (
        "指标端点 /metrics 已拒绝所有抓取：生产环境需设置 "
        "SUPERVISION_METRICS_BEARER_TOKEN（并与 deploy/.env 的 METRICS_BEARER_TOKEN 一致），"
        "否则 Prometheus 会持续 403、面板无数据。"
    )


# --------------------------------------------------------------------------- #
# 指标采集
# --------------------------------------------------------------------------- #
def collect_metrics(session) -> list[str]:
    """采集并渲染全部指标。"""
    from app.db.models import DocChunk, EvalReport, EvalTask, JudgeReview, SpecDoc
    from app.retrieval.vector_store import discover_namespaces, index_stats

    lines: list[str] = []

    # ---- 文档与分块 ----
    doc_count = int(session.execute(select(func.count(SpecDoc.id))).scalar() or 0)
    chunk_count = int(session.execute(select(func.count(DocChunk.id))).scalar() or 0)
    lines += render_metric(
        "supervision_documents_total", doc_count,
        help_text="规范文档总数",
    )
    lines += render_metric(
        "supervision_chunks_total", chunk_count,
        help_text="文档切分后的分块总数",
    )

    # ---- 评估任务（按状态） ----
    task_rows = session.execute(
        select(EvalTask.current_state, func.count(EvalTask.id)).group_by(EvalTask.current_state)
    ).all()
    lines.append("# HELP supervision_eval_tasks 评估任务数量（按状态）")
    lines.append("# TYPE supervision_eval_tasks gauge")
    total_tasks = 0
    for state_name, count in task_rows:
        total_tasks += int(count)
        lines.append(
            f'supervision_eval_tasks{{state="{_escape_label(str(state_name))}"}} {_fmt(int(count))}'
        )
    lines += render_metric("supervision_eval_tasks_total", total_tasks, help_text="评估任务总数")

    # ---- Token 消耗 ----
    token_sum = session.execute(
        select(func.coalesce(func.sum(EvalTask.total_tokens), 0))
    ).scalar()
    lines += render_metric(
        "supervision_llm_tokens_total", int(token_sum or 0),
        help_text="累计消耗的大模型 Token 数",
    )
    cost_sum = session.execute(
        select(func.coalesce(func.sum(EvalTask.total_cost), 0))
    ).scalar()
    lines += render_metric(
        "supervision_llm_cost_total", float(cost_sum or 0),
        help_text="累计评估成本（计价单位由上游模型账单决定）",
    )

    # ---- 报告与评审 ----
    report_count = int(session.execute(select(func.count(EvalReport.id))).scalar() or 0)
    lines += render_metric("supervision_reports_total", report_count, help_text="生成的评估报告总数")
    review_rows = session.execute(
        select(JudgeReview.grade, func.count(JudgeReview.id)).group_by(JudgeReview.grade)
    ).all()
    lines.append("# HELP supervision_judge_reviews Judge 评审数量（按等级）")
    lines.append("# TYPE supervision_judge_reviews gauge")
    for grade, count in review_rows:
        lines.append(
            f'supervision_judge_reviews{{grade="{_escape_label(str(grade))}"}} {_fmt(int(count))}'
        )
    needs_human = int(
        session.execute(
            select(func.count(JudgeReview.id)).where(JudgeReview.needs_human.is_(True))
        ).scalar()
        or 0
    )
    lines += render_metric(
        "supervision_judge_needs_human_total", needs_human,
        help_text="转人工复核的评审数量（可信度预警）",
    )

    # ---- 向量索引（按知识库分片） ----
    namespaces = discover_namespaces()
    lines.append("# HELP supervision_vector_index_size 各知识库分片的向量条数")
    lines.append("# TYPE supervision_vector_index_size gauge")
    total_vectors = 0
    dim = 0
    dirty = False
    for name in namespaces:
        stats = index_stats(name)
        size = int(stats.get("size") or 0)
        total_vectors += size
        dim = dim or int(stats.get("dim") or 0)
        dirty = dirty or bool(stats.get("dirty"))
        lines.append(
            f'supervision_vector_index_size{{namespace="{_escape_label(name)}"}} {_fmt(size)}'
        )
    lines += render_metric("supervision_vector_index_total", total_vectors, help_text="全部向量总数")
    lines += render_metric("supervision_vector_dim", dim, help_text="向量维度（0 表示索引未建立）")
    lines += render_metric(
        "supervision_vector_index_dirty", 1 if dirty else 0,
        help_text="索引是否有未落盘的变更（1=是）",
    )

    # ---- 组件健康 ----
    from app.db.session import ping

    lines += render_metric("supervision_db_up", 1 if ping() else 0, help_text="数据库连通性（1=正常）")
    try:
        from app.kg.graph_store import get_graph_store

        graph_ok = bool(get_graph_store().available)
    except Exception:  # noqa: BLE001
        graph_ok = False
    lines += render_metric("supervision_neo4j_up", 1 if graph_ok else 0, help_text="Neo4j 可用性（1=正常）")

    # ---- 错误码字典（便于 Grafana 展示口径） ----
    lines.append("# HELP supervision_error_code_info 错误码字典（值恒为 1）")
    lines.append("# TYPE supervision_error_code_info gauge")
    for code, message in list(ERROR_CODES.items())[:50]:
        lines.append(
            f'supervision_error_code_info{{code="{_escape_label(str(code))}",'
            f'message="{_escape_label(str(message))}"}} 1'
        )

    return lines


# --------------------------------------------------------------------------- #
# 端点
# --------------------------------------------------------------------------- #
@router.get("/metrics", summary="Prometheus 指标（文本格式）", include_in_schema=False)
def prometheus_metrics(
    request: Request,
    authorization: Optional[str] = Header(None),
) -> Response:
    from app.db.session import get_session_factory

    allowed, reason = authorize_scrape(authorization, request.client.host if request.client else None)
    if not allowed:
        logger.warning("指标抓取被拒绝", extra={"reason": reason})
        return Response(
            content=(
                "# 指标抓取未授权\n"
                f"# 原因: {reason}\n"
                "# 配置方式（二选一）：\n"
                "#   1) 设置 SUPERVISION_METRICS_BEARER_TOKEN，并让 Prometheus 带上\n"
                "#      Authorization: Bearer <token>（推荐，见 deploy/prometheus.yml）\n"
                "#   2) 设置 SUPERVISION_METRICS_PUBLIC=true（仅限内网/开发环境）\n"
            ),
            status_code=403,
            media_type=CONTENT_TYPE,
        )

    session = get_session_factory()()
    try:
        body = "\n".join(collect_metrics(session)) + "\n"
    except Exception as exc:  # noqa: BLE001 - 采集失败不能让 Prometheus 拿到 500 而丢历史
        logger.error("指标采集失败", extra={"error": f"{type(exc).__name__}: {str(exc)[:300]}"})
        body = (
            "# 指标采集失败，仅上报错误标记\n"
            + "\n".join(
                render_metric(
                    "supervision_metrics_scrape_error", 1,
                    help_text="指标采集失败（1=失败）",
                )
            )
            + "\n"
        )
    finally:
        session.close()

    return Response(content=body, media_type=CONTENT_TYPE)
