# -*- coding: utf-8 -*-
"""评估任务执行器：驱动 LangGraph 工作流并把结果落库（SRS FR-AGT-08/13）。

- 检查点：PostgreSQL（langgraph-checkpoint-postgres）优先，不可用时降级 InMemorySaver，
  降级会在返回结果中标注，不静默失真；
- 结果：eval_task 状态、eval_subtask、match_result、agent_step_log、eval_report 全部落库；
- 断点续跑：同一 thread_id 恢复，守卫计数器沿用不重置（避免绕过上限）。
"""
from __future__ import annotations

import asyncio
import sys
import time
import uuid
from typing import Any, Optional

from sqlalchemy.exc import InterfaceError, OperationalError, PendingRollbackError
from sqlalchemy.orm import Session

from app.agent.graph import AgentContext, AgentState, agent_context, get_agent_graph
from app.agent.guards import ConvergenceGuard, GuardPolicy
from app.agent.state import StateMachine, TaskState
from app.config import settings
from app.constants import AgentStep, EvalState
from app.core.logging_conf import get_logger
from app.db import utcnow
from app.db.models import (
    AgentStepLog,
    AgentToolCall,
    EvalReport,
    EvalSubtask,
    EvalTask,
    MatchResult,
    User,
)
from app.llm.gateway import current_usage, start_usage_session
from app.retrieval.pipeline import RetrievalPipeline

logger = get_logger(__name__)

_saver_cache: dict[str, Any] = {}
_saver_keepalive: dict[str, Any] = {}
_saver_loop: dict[str, Any] = {}


async def _checkpoint_schema_ready(pool) -> bool:
    """探测检查点表结构是否已就绪（无需执行 DDL）。

    背景（真实踩坑）：``AsyncPostgresSaver.setup()`` 会执行

        CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoints_thread_id_idx ...

    而 ``CREATE INDEX CONCURRENTLY`` **不能回滚，且必须等待所有并发事务结束**。
    容器化部署里只要还有别的连接处于 ``idle in transaction``（例如一个未及时
    关闭的 SQLAlchemy 会话），这条语句就会永久等待；多个重试连接再叠加上去，
    便形成锁死——检索/Agent 全部挂起，直到连接被强制清理。

    因此这里先只读探测迁移表与迁移数量：已全部应用就直接跳过 setup。
    """
    try:
        from langgraph.checkpoint.postgres.base import BasePostgresSaver

        total = len(getattr(BasePostgresSaver, "MIGRATIONS", []) or [])
        async with pool.connection() as conn:
            async with conn.cursor() as cur:
                await cur.execute("SELECT count(*) FROM checkpoint_migrations")
                row = await cur.fetchone()
        applied = int(row[0]) if row else 0
        if total and applied >= total:
            return True
        logger.info(
            "检查点迁移未完成，将执行 setup()",
            extra={"applied": applied, "total": total},
        )
        return False
    except Exception:  # noqa: BLE001 - 表不存在或探测失败 => 需要 setup
        return False


async def _build_async_postgres_saver():
    """构建异步 PostgreSQL 检查点。

    Windows 默认事件循环是 ProactorEventLoop，psycopg 异步模式不支持，因此先尝试
    切换为 SelectorEventLoop（必须发生在事件循环创建之前）。
    """
    _ensure_selector_event_loop_policy()
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
    from psycopg_pool import AsyncConnectionPool

    # 显式自建连接池（而非 from_conn_string），以便控制连接行为。
    #
    # 注意：这里**刻意不设** idle_in_transaction_session_timeout / statement_timeout。
    #   检查点连接是 autocommit 的短事务，本身不会长时间停在 idle in transaction；
    #   而一旦设了阈值，长评估任务（数分钟）中途就会被数据库断开连接，
    #   导致 Agent 跑到一半降级为内存检查点，或落库时抛 PendingRollbackError。
    #   历史教训：
    #     - statement_timeout=120s  → Agent 中途降级为内存检查点
    #     - idle_in_transaction=60s → PG 日志 FATAL: terminating connection
    #                                 due to idle-in-transaction timeout，CI 测试失败
    #   真正需要防的「长事务拖住 CREATE INDEX CONCURRENTLY」已由
    #   _checkpoint_schema_ready() 先探测迁移状态、就绪即跳过 setup() 解决，
    #   不再依赖连接级超时这种会误伤正常业务的粗暴手段。
    pool = AsyncConnectionPool(
        conninfo=_psycopg_dsn(),
        min_size=1,
        max_size=8,
        kwargs={
            "autocommit": True,
            "prepare_threshold": 0,
        },
        open=False,
        timeout=30,
    )
    await pool.open(wait=True, timeout=max(15, settings.db_connect_timeout or 10))

    saver = AsyncPostgresSaver(pool)
    if not await _checkpoint_schema_ready(pool):
        # 仅在迁移缺失时才跑 DDL；并加超时兜底，绝不允许无限挂起
        await asyncio.wait_for(saver.setup(), timeout=180)
    else:
        logger.info("检查点表结构已就绪，跳过 setup()（避免 CREATE INDEX CONCURRENTLY 锁等待）")

    _saver_keepalive["actx"] = pool
    _saver_loop["loop"] = asyncio.get_running_loop()
    return saver


def _ensure_selector_event_loop_policy() -> None:
    """Windows 上把事件循环策略切到 Selector，供 psycopg 异步连接使用。

    必须在事件循环创建前调用；如果当前已有运行中的 Proactor 循环，则无法切换，
    此时调用方会降级为内存检查点（并给出明确日志）。
    """
    if sys.platform != "win32":
        return
    if type(asyncio.get_event_loop_policy()) is asyncio.WindowsSelectorEventLoopPolicy:
        return
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        logger.info("Windows 事件循环策略已切换为 SelectorEventLoop（psycopg 异步需要）")
        return
    logger.warning(
        "当前已是 ProactorEventLoop，无法切换为 SelectorEventLoop，"
        "PostgreSQL 检查点不可用（改用内存检查点或设置 SUPERVISION_CHECKPOINT_BACKEND）"
    )


async def get_checkpointer_async(prefer: Optional[str] = None) -> tuple[Any, str]:
    """异步版检查点选择（Agent 是异步图，必须用异步 saver）。"""
    backend = (prefer or settings.checkpoint_backend or "auto").lower()
    if backend == "memory" or settings.is_sqlite:
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver(), "memory"

    if backend in {"auto", "postgres"}:
        key = "postgres"
        cached = _saver_cache.get(key)
        if cached is not None and _saver_loop.get("loop") is asyncio.get_running_loop():
            return cached, key
        if cached is not None:
            # 事件循环已变化，丢弃旧 saver（连接池不可跨循环复用）
            _saver_cache.pop(key, None)
            _saver_keepalive.pop("actx", None)
        try:
            saver = await _build_async_postgres_saver()
            _saver_cache[key] = saver
            logger.info("LangGraph 检查点使用 PostgreSQL 持久化（异步）")
            return saver, key
        except Exception as exc:  # noqa: BLE001
            if backend == "postgres":
                raise
            logger.warning(
                "PostgreSQL 检查点不可用，降级为内存检查点（重启后无法续跑）",
                extra={"error": f"{type(exc).__name__}: {str(exc)[:200]}"},
            )
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver(), "memory"


def get_checkpointer(prefer: Optional[str] = None) -> tuple[Any, str]:
    """同步场景（脚本/诊断）使用的检查点选择。"""
    backend = (prefer or settings.checkpoint_backend or "auto").lower()
    if backend == "memory" or settings.is_sqlite:
        from langgraph.checkpoint.memory import InMemorySaver

        return InMemorySaver(), "memory"
    if backend in {"auto", "postgres"}:
        try:
            from langgraph.checkpoint.postgres import PostgresSaver

            # 与异步版本保持一致：显式连接池，但不设连接级超时（原因见
            # _build_async_postgres_saver 的注释：会误杀长任务的正常连接）
            from psycopg_pool import ConnectionPool

            pool = ConnectionPool(
                conninfo=_psycopg_dsn(),
                min_size=1,
                max_size=4,
                kwargs={
                    "autocommit": True,
                    "prepare_threshold": 0,
                },
                open=False,
                timeout=30,
            )
            pool.open(wait=True, timeout=max(15, settings.db_connect_timeout or 10))
            saver = PostgresSaver(pool)
            if not _checkpoint_schema_ready_sync(pool):
                saver.setup()
            else:
                logger.info("检查点表结构已就绪，跳过 setup()（同步）")
            _saver_keepalive["sctx"] = pool
            return saver, "postgres"
        except Exception as exc:  # noqa: BLE001
            if backend == "postgres":
                raise
            logger.warning(
                "PostgreSQL 检查点不可用，降级为内存检查点",
                extra={"error": f"{type(exc).__name__}: {str(exc)[:200]}"},
            )
    from langgraph.checkpoint.memory import InMemorySaver

    return InMemorySaver(), "memory"


def _psycopg_dsn() -> str:
    """把 SQLAlchemy URL 转成 psycopg 连接串。"""
    url = settings.database_url
    url = url.replace("postgresql+psycopg://", "postgresql://")
    url = url.replace("postgresql+asyncpg://", "postgresql://")
    return url


def _checkpoint_schema_ready_sync(pool) -> bool:
    """同步版：探测检查点迁移是否已全部应用（原因见异步版同名函数）。"""
    try:
        from langgraph.checkpoint.postgres.base import BasePostgresSaver

        total = len(getattr(BasePostgresSaver, "MIGRATIONS", []) or [])
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT count(*) FROM checkpoint_migrations")
                row = cur.fetchone()
        applied = int(row[0]) if row else 0
        return bool(total) and applied >= total
    except Exception:  # noqa: BLE001
        return False


def _restore_guard_state(snapshot: Optional[dict]) -> ConvergenceGuard:
    """从历史快照恢复守卫状态：计数器沿用不重置（SRS 4.4）。"""
    policy = GuardPolicy()
    guard = ConvergenceGuard(policy)
    snapshot = snapshot or {}
    guard.iteration_count = int(snapshot.get("iteration_count") or 0)
    guard.no_progress_rounds = int(snapshot.get("no_progress_rounds") or 0)
    guard.token_used = int(snapshot.get("token_used") or 0)
    return guard


def _persist_subtasks(session: Session, task_id: uuid.UUID, subtasks: list[dict]) -> None:
    session.query(EvalSubtask).filter(EvalSubtask.task_id == task_id).delete()
    for item in subtasks:
        session.add(
            EvalSubtask(
                task_id=task_id,
                seq=int(item.get("seq") or 0),
                name=str(item.get("name") or "")[:255],
                criterion=item.get("criterion"),
                required_evidence=item.get("required_evidence"),
                specialty=item.get("specialty"),
                query=item.get("query"),
                status="done",
            )
        )


def _persist_matches(session: Session, task_id: uuid.UUID, matches: list[dict]) -> None:
    session.query(MatchResult).filter(MatchResult.task_id == task_id).delete()
    for item in matches:
        citation = item.get("citation") or {}
        session.add(
            MatchResult(
                task_id=task_id,
                clause_id=citation.get("chunk_id"),
                clause_no=item.get("clause_no"),
                spec_code=item.get("spec_code") or citation.get("spec_code"),
                spec_name=citation.get("spec_name"),
                verdict=item.get("verdict") or "insufficient_evidence",
                confidence=item.get("confidence"),
                relevance_score=item.get("relevance_score"),
                evidence=item.get("evidence"),
                reasoning=item.get("reasoning"),
                risk_level=item.get("risk_level"),
                remediation=item.get("remediation"),
                citation_json=citation or None,
            )
        )


def _collect_basis(matches: list[dict]) -> list[dict]:
    """汇总去重后的评估依据清单（用于报告附录与引用校验兜底）。"""
    basis: list[dict] = []
    seen: set[str] = set()
    for match in matches:
        citation = match.get("citation") or {}
        if not citation:
            continue
        key = f"{citation.get('spec_code')}|{citation.get('clause_no')}|{citation.get('chunk_id')}"
        if key in seen:
            continue
        seen.add(key)
        location_parts = []
        if citation.get("chapter_path"):
            location_parts.append(str(citation["chapter_path"]))
        if citation.get("page_no"):
            location_parts.append(f"P{citation['page_no']}")
        basis.append(
            {
                "spec_code": citation.get("spec_code"),
                "spec_name": citation.get("spec_name"),
                "clause_no": citation.get("clause_no"),
                "location": " / ".join(location_parts) or "-",
                "chunk_id": citation.get("chunk_id"),
            }
        )
    return basis


def _persist_report(
    session: Session,
    task_id: uuid.UUID,
    report: dict,
    markdown: str,
    matches: list[dict],
    generator_model: str,
) -> EvalReport:
    """写入/更新评估报告。

    只依赖 `task_id` 而不是 ORM 对象：会话经历 rollback（连接失效恢复）后，
    再访问 ORM 属性会触发一次刷新查询，而那时连接可能仍然不可用。
    """
    existing = session.query(EvalReport).filter(EvalReport.task_id == task_id).one_or_none()
    basis_count = len({(m.get("spec_code"), m.get("clause_no")) for m in matches if m.get("clause_no")})
    non_compliance = sum(
        1 for m in matches if m.get("verdict") in {"non_compliant", "partial", "insufficient_evidence"}
    )
    payload = {
        "conclusion": report.get("conclusion"),
        "overall_verdict": report.get("overall_verdict"),
        "risk_level": report.get("risk_level"),
        "summary": (report.get("analysis") or {}).get("summary"),
        "content": {
            **report,
            # 关键：把条款比对结果一并存入报告内容。
            # Judge 的引用校验依赖它逐条确认「引用是否真实存在」，
            # 缺失时会导致引用校验拿不到任何引用（幻觉识别形同失效）。
            "matches": list(matches),
            "evidence_basis": _collect_basis(matches),
        },
        "markdown": markdown,
        "basis_count": basis_count,
        "non_compliance_count": non_compliance,
        "generator_model": generator_model,
    }
    if existing is None:
        existing = EvalReport(task_id=task_id, **payload)
        session.add(existing)
    else:
        for key, value in payload.items():
            setattr(existing, key, value)
        # 重新生成报告时**必须清除上一次的人工裁定**。
        #
        # 否则会留下这样的矛盾数据：任务是 NEED_HUMAN（待复核），
        # 报告却带着上一版的 human_verdict=qualified 和 is_final=True ——
        # 等于新报告被凭空继承了旧签认，而它**从未被人看过**。
        # 这比「少一个字段」严重：签发记录的语义被污染了。
        #
        # 重跑产生的是**新报告**，理应重新走复核；历次裁定在 human_feedback
        # 与 audit_log 中已留痕，不会因为清空当前字段而丢失追溯链。
        existing.human_verdict = None
        existing.review_comment = None
        existing.reviewed_by = None
        existing.reviewed_at = None
        existing.is_final = False
    return existing


def _persist_step_logs(session: Session, task_id: uuid.UUID, state: dict, timings: dict[str, int]) -> None:
    session.query(AgentStepLog).filter(AgentStepLog.task_id == task_id).delete()
    order = [
        (AgentStep.PLANNING.value, "planning"),
        (AgentStep.RETRIEVAL.value, "retrieval"),
        (AgentStep.MATCHING.value, "clause_matching"),
        (AgentStep.ANALYSIS.value, "analysis"),
        (AgentStep.REPORT.value, "report_generation"),
    ]
    for seq, (step, key) in enumerate(order, start=1):
        duration = timings.get(key)
        if duration is None and step != AgentStep.PLANNING.value:
            # 守卫提前终止时后续节点未执行
            status = "skipped"
        else:
            status = "done"
        session.add(
            AgentStepLog(
                task_id=task_id,
                step=step,
                seq=seq,
                status=status,
                duration_ms=duration,
                iteration=state.get("iteration_count") or 0,
                output_digest=_digest(step, state),
            )
        )


def _persist_tool_calls(session: Session, task_id: uuid.UUID, records: list) -> None:
    """落库 Agent 工具调用审计（对应权限设计的「工具调用权限」）。

    与 ``_persist_step_logs`` 一样先删后写：支持断点续跑时重放，避免重复累积。
    审计记录本身是「本次执行实际发生的调用流水」，重跑就该重新记录。
    """
    if not records:
        return
    session.query(AgentToolCall).filter(AgentToolCall.task_id == task_id).delete()
    for seq, record in enumerate(records, start=1):
        session.add(
            AgentToolCall(
                task_id=task_id,
                seq=seq,
                tool=getattr(record, "tool", ""),
                allowed=bool(getattr(record, "allowed", False)),
                user_id=getattr(record, "user_id", None),
                params=getattr(record, "params", None),
                reason=(getattr(record, "reason", "") or None),
                duration_ms=getattr(record, "duration_ms", None),
                output_digest=(getattr(record, "output_digest", "") or None),
            )
        )


def _digest(step: str, state: dict) -> dict:
    if step == AgentStep.PLANNING.value:
        return {"subtasks": len(state.get("subtasks") or [])}
    if step == AgentStep.RETRIEVAL.value:
        results = state.get("retrieval_results") or {}
        return {
            "queries": len(results),
            "clauses": sum(len((v or {}).get("clauses") or []) for v in results.values()),
        }
    if step == AgentStep.MATCHING.value:
        matches = state.get("matches") or []
        return {
            "matches": len(matches),
            "non_compliant": sum(1 for m in matches if m.get("verdict") == "non_compliant"),
        }
    if step == AgentStep.ANALYSIS.value:
        analysis = state.get("analysis") or {}
        return {"findings": len(analysis.get("findings") or []), "risk": analysis.get("risk_level")}
    if step == AgentStep.REPORT.value:
        return {"has_report": bool(state.get("report")), "markdown_chars": len(state.get("markdown") or "")}
    return {}


async def run_evaluation(
    session: Session,
    task: EvalTask,
    *,
    resume: bool = False,
    checkpointer_preference: Optional[str] = None,
) -> dict[str, Any]:
    """执行一次评估任务（或从检查点续跑）。

    外层负责追踪，真正的实现在 ``_run_evaluation_core``。
    这样做的原因：函数体有 200 多行，若为插入追踪而整体重新缩进，
    风险远大于收益；薄包装则完全不动原逻辑。
    """
    from app.core.tracing import trace_span

    task_id = getattr(task, "id", None)
    with trace_span(
        "agent.evaluate",
        run_type="chain",
        inputs={
            "task_id": str(task_id) if task_id else None,
            "eval_type": getattr(task, "eval_type", None),
            "title": getattr(task, "title", None),
            "resume": resume,
        },
        tags=["agent", str(getattr(task, "eval_type", "") or "unknown")],
        metadata={
            "project_id": getattr(task, "project_id", None),
            "user_id": getattr(task, "user_id", None),
        },
    ) as span:
        result = await _run_evaluation_core(
            session, task, resume=resume, checkpointer_preference=checkpointer_preference
        )
        # 只上报关键字段：完整 result 里有大量中间产物，全部上报会撑爆追踪
        span.outputs = {
            k: result.get(k)
            for k in (
                "current_state",
                "iteration_count",
                "matches",
                "token_used",
                "elapsed_ms",
                "checkpoint_backend",
                "degraded",
                "report_id",
                "guard_reason",
            )
            if k in result
        }
        return result


async def _run_evaluation_core(
    session: Session,
    task: EvalTask,
    *,
    resume: bool = False,
    checkpointer_preference: Optional[str] = None,
) -> dict[str, Any]:
    """执行一次评估任务（或从检查点续跑）。"""
    started = time.perf_counter()
    payload = dict(task.input_payload or {})
    options = dict(task.options or {})
    if task.specialty:
        payload.setdefault("specialty", task.specialty)

    policy = GuardPolicy.from_options(options)
    if resume:
        guard = _restore_guard_state(task.state_snapshot)
        guard.policy = policy
    else:
        guard = ConvergenceGuard(policy)
    thread_id = task.checkpoint_thread_id or str(task.id)
    task.checkpoint_thread_id = thread_id

    start_usage_session()
    saver, backend = await get_checkpointer_async(checkpointer_preference)
    graph = get_agent_graph(saver, cache_key=f"agent-{backend}")
    # 未显式指定 namespace 时跨全部分片检索（多知识库），由 kb_ids 在 SQL 层再过滤
    pipeline = RetrievalPipeline(namespace=options.get("namespace"))

    machine = StateMachine(TaskState(task_id=str(task.id), thread_id=thread_id))
    if resume and task.state_snapshot:
        try:
            machine.state.current_state = EvalState(task.current_state)
        except ValueError:
            machine.state.current_state = EvalState.PENDING
    machine.transition(EvalState.PENDING, enforce=False)
    machine.transition(EvalState.PLANNING)

    initial: AgentState = {
        "task_id": str(task.id),
        "thread_id": thread_id,
        "payload": payload,
        "options": options,
        "subtasks": [],
        "retrieval_results": {},
        "matches": [],
        "analysis": {},
        "report": {},
        "iteration_count": guard.iteration_count,
        "no_progress_rounds": guard.no_progress_rounds,
        "token_used": guard.token_used,
        "errors": [],
        "degraded": False,
        "started_at": time.time(),
        "state": {"current_state": EvalState.PLANNING.value},
    }

    task.started_at = task.started_at or utcnow()
    # 注入发起人身份：工具级授权需要它（见 agent/tools.py）。
    # 用任务上的 user_id 反查，避免改动 run_evaluation 的签名（那会牵动多处调用）。
    actor = None
    task_user_id = getattr(task, "user_id", None)
    if task_user_id is not None:
        try:
            actor = session.get(User, task_user_id)
        except Exception as exc:  # noqa: BLE001 - 查不到用户时按最小权限处理
            logger.warning("加载评估发起人失败", extra={"error": str(exc)[:200]})
            actor = None

    ctx = AgentContext(
        guard=guard, session=session, pipeline=pipeline, timings={}, user=actor
    )

    final_state: dict[str, Any] = dict(initial)
    error: Optional[str] = None
    try:
        with agent_context(ctx):
            result = await graph.ainvoke(
                initial,
                config={
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": max(25, policy.max_iterations * 4),
                },
            )
        final_state = dict(result or initial)
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
        logger.error("Agent 执行失败", extra={"task_id": str(task.id), "error": error[:400]})
        machine.record_error(error_type="agent_execution", message=error)
        final_state.setdefault("errors", []).append(
            {"step": "runtime", "type": "exception", "message": error[:500], "fatal": True}
        )

    usage = current_usage()
    guard.add_tokens(0)

    # 状态收敛
    state_info = final_state.get("state") or {}
    target_name = state_info.get("current_state") or EvalState.FAILED.value
    if error and target_name not in {EvalState.DEGRADED.value, EvalState.NEED_HUMAN.value}:
        target_name = EvalState.FAILED.value
    try:
        target_state = EvalState(target_name)
    except ValueError:
        target_state = EvalState.FAILED
    from app.constants import STATE_TRANSITIONS

    if target_state not in STATE_TRANSITIONS.get(machine.state.current_state, set()) and target_state != machine.state.current_state:
        machine.transition(target_state, enforce=False)
    else:
        machine.transition(target_state, enforce=False)

    # 落库
    subtasks = final_state.get("subtasks") or []
    matches = final_state.get("matches") or []

    # 预先取出纯量：rollback 之后访问 ORM 属性会触发刷新查询，而那时连接可能仍不可用。
    task_id = task.id
    markdown_out = final_state.get("markdown") or ""
    prev_step = task.current_step
    prev_finished_at = task.finished_at
    snapshot = {
        **machine.state.to_snapshot(),
        "guard": guard.summary(),
        "errors": final_state.get("errors") or [],
        "timings": ctx.timings,
        "checkpoint_backend": backend,
    }

    def _apply_task_fields() -> None:
        """写回任务字段（可重放：rollback 会使对象过期，需重新赋值才能持久化）。"""
        task.current_state = target_state.value
        task.current_step = state_info.get("current_step") or prev_step
        task.iteration_count = int(final_state.get("iteration_count") or guard.iteration_count)
        task.no_progress_rounds = int(final_state.get("no_progress_rounds") or guard.no_progress_rounds)
        task.total_tokens = usage.total_tokens or guard.token_used
        task.error_state = machine.state.error_state
        task.guard_reason = state_info.get("guard_reason")
        task.progress = _progress_of(target_state)
        task.finished_at = (
            utcnow()
            if target_state.value in {"COMPLETED", "FAILED", "CANCELLED", "DEGRADED"}
            else prev_finished_at
        )
        machine.state.iteration_count = task.iteration_count
        machine.state.no_progress_rounds = task.no_progress_rounds
        machine.state.token_used = task.total_tokens
        task.state_snapshot = snapshot

    _apply_task_fields()

    def _write_results() -> Optional[EvalReport]:
        """把本次执行结果落库（可安全重放）。"""
        if subtasks:
            _persist_subtasks(session, task_id, subtasks)
        if matches:
            _persist_matches(session, task_id, matches)
        _persist_step_logs(session, task_id, final_state, ctx.timings)
        _persist_tool_calls(session, task_id, ctx.tool_calls)
        row = None
        if final_state.get("report"):
            if not markdown_out:
                logger.error(
                    "报告 markdown 为空，请检查 report_node 输出",
                    extra={
                        "task_id": str(task_id),
                        "state_keys": sorted(final_state.keys()),
                        "report_keys": sorted((final_state.get("report") or {}).keys()),
                    },
                )
            row = _persist_report(
                session,
                task_id,
                final_state["report"],
                markdown_out,
                matches,
                generator_model=settings.llm_primary_model,
            )
        session.flush()
        return row

    # 关键韧性：Agent 图执行可能持续数分钟（多次 LLM 调用），期间请求会话持有的
    # 数据库连接可能已被服务端回收（空闲事务超时、连接池回收、网络抖动）。
    # 这会让事务进入「失效」状态，此后任何 SQL 都抛
    #   sqlalchemy.exc.PendingRollbackError: Can't reconnect until invalid transaction is rolled back
    # （CI 上曾稳定复现，PostgreSQL 日志对应：
    #   FATAL: terminating connection due to idle-in-transaction timeout）
    #
    # 处理方式（顺序很关键，实测依据见 scripts/probe_connection_recovery.py）：
    #   必须**先** session.invalidate() 丢弃已死的连接，再 rollback() 复位事务状态，
    #   然后重放「任务字段 + 结果落库」。
    #   实测对比（同一故障场景各 3 次）：
    #     invalidate()              → 3/3 成功
    #     invalidate() + rollback() → 3/3 成功
    #     rollback() + invalidate() → 0/3 失败
    #     close()    + invalidate() → 0/3 失败
    #   原因：rollback()/close() 都会在已死的连接上再发一次 ROLLBACK 命令而报
    #   AdminShutdown，反而阻断恢复；只有先 invalidate() 才能丢弃坏连接。
    #
    # 重放安全性：落库函数是「先删后插」的幂等写法，且此刻事务尚未提交，
    # 因此重试不会产生重复数据。
    try:
        report_row = _write_results()
    except (PendingRollbackError, OperationalError, InterfaceError) as exc:
        logger.warning(
            "落库时会话连接已失效，丢弃坏连接后重试一次（长任务期间连接被回收属正常现象）",
            extra={"task_id": str(task_id), "error": f"{type(exc).__name__}: {str(exc)[:160]}"},
        )
        session.invalidate()
        session.rollback()
        _apply_task_fields()
        report_row = _write_results()
    elapsed_ms = int((time.perf_counter() - started) * 1000)
    logger.info(
        "评估任务执行结束",
        extra={
            "task_id": str(task.id),
            "state": target_state.value,
            "iterations": task.iteration_count,
            "no_progress_rounds": task.no_progress_rounds,
            "matches": len(matches),
            "tokens": task.total_tokens,
            "elapsed_ms": elapsed_ms,
            "checkpoint_backend": backend,
        },
    )
    return {
        "task_id": str(task.id),
        "thread_id": thread_id,
        "current_state": target_state.value,
        "iteration_count": task.iteration_count,
        "no_progress_rounds": task.no_progress_rounds,
        "subtasks": subtasks,
        "matches": matches,
        "analysis": final_state.get("analysis") or {},
        "report": final_state.get("report") or {},
        "markdown": final_state.get("markdown") or "",
        "report_id": str(report_row.id) if report_row is not None else None,
        "guard": guard.summary(),
        "errors": final_state.get("errors") or [],
        "token_used": task.total_tokens,
        "elapsed_ms": elapsed_ms,
        "checkpoint_backend": backend,
        "degraded": bool(final_state.get("degraded")),
    }


def _progress_of(state: EvalState) -> float:
    """状态 → 进度（委托 ``constants.progress_of``，保持单一实现来源）。

    进度映射放在 constants 是为了让人工裁定等非 Agent 路径也能同步 ——
    否则签发后会出现「COMPLETED 但 progress=0.9」的不一致。
    """
    from app.constants import progress_of

    return progress_of(state)


async def run_evaluation_async(task_id: uuid.UUID, *, resume: bool = False) -> None:
    """后台执行入口：自建会话，供队列/后台任务调用。"""
    from app.db.session import session_scope

    with session_scope() as session:
        task = session.get(EvalTask, task_id)
        if task is None:
            logger.warning("任务不存在，跳过执行", extra={"task_id": str(task_id)})
            return
        await run_evaluation(session, task, resume=resume)


def submit_background(task_id: uuid.UUID, *, resume: bool = False) -> None:
    """把任务提交到事件循环后台执行（FR-AGT-12 并发控制由信号量在网关层约束）。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        asyncio.run(run_evaluation_async(task_id, resume=resume))
        return
    loop.create_task(run_evaluation_async(task_id, resume=resume))


__all__ = [
    "run_evaluation",
    "run_evaluation_async",
    "submit_background",
    "get_checkpointer",
]
