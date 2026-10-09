# -*- coding: utf-8 -*-
"""合规评估 Agent —— LangGraph 五阶段工作流（SRS FR-AGT-02）。

节点：planning → retrieval → clause_matching → analysis → report_generation
每个节点都受 :class:`ConvergenceGuard` 约束，触发守卫时跳到 finalize，
按 SRS 4.1 以 DEGRADED / NEED_HUMAN / FAILED 收敛，绝不进入死循环。
"""
from __future__ import annotations

import asyncio
import contextlib
import contextvars
import json
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Iterator, Optional, Sequence, TypedDict

from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from sqlalchemy.orm import Session

from app.agent import report as report_renderer
from app.agent.guards import ConvergenceGuard, GuardPolicy, ProgressSnapshot, count_new_items
from app.agent.state import StateMachine, TaskState
from app.config import settings
from app.constants import EvalState, Verdict
from app.core.logging_conf import get_logger
from app.db import utcnow
from app.llm.gateway import ChatMessage, current_usage, get_llm, start_usage_session
from app.llm.prompts import (
    AGENT_ANALYSIS_SYSTEM,
    AGENT_ANALYSIS_USER,
    AGENT_MATCHING_SYSTEM,
    AGENT_MATCHING_USER,
    AGENT_PLANNING_SYSTEM,
    AGENT_PLANNING_USER,
    AGENT_REPORT_SYSTEM,
    AGENT_REPORT_USER,
)
from app.retrieval.pipeline import RetrievalPipeline, assemble_context

logger = get_logger(__name__)

MAX_EVIDENCE_CHARS = 4000
MAX_CLAUSE_PREVIEW = 700


def _append(left: list, right: list) -> list:
    """LangGraph reducer：列表增量合并。"""
    return (left or []) + (right or [])


@dataclass
class AgentContext:
    """节点运行时依赖（守卫、数据库会话、检索管道、耗时统计）。

    这些对象不可序列化，因此通过 ContextVar 传递，而不是放进 LangGraph 状态，
    否则检查点无法持久化，断点续跑（FR-AGT-08）会失效。
    """

    guard: ConvergenceGuard
    session: Session
    pipeline: RetrievalPipeline
    timings: dict
    #: 发起评估的用户。用于工具级授权（见 agent/tools.py）。
    #: 为空表示由后台任务触发（如无请求上下文的续跑），此时工具按最小权限处理。
    user: Optional[Any] = None
    #: 工具调用审计记录（调用结束后由 runner 落库）
    tool_calls: list = field(default_factory=list)


_ctx: contextvars.ContextVar[Optional[AgentContext]] = contextvars.ContextVar("agent_ctx", default=None)


def get_ctx() -> AgentContext:
    ctx = _ctx.get()
    if ctx is None:  # pragma: no cover - 正常流程不会发生
        raise RuntimeError("Agent 运行时上下文未初始化")
    return ctx


@contextlib.contextmanager
def agent_context(ctx: AgentContext) -> Iterator[AgentContext]:
    token = _ctx.set(ctx)
    try:
        yield ctx
    finally:
        _ctx.reset(token)


class AgentState(TypedDict, total=False):
    """图状态。

    注意：LangGraph 只保留在此声明的键，节点返回未声明的键会被**静默丢弃**。
    新增节点输出时必须同步在此声明，否则结果不会进入最终状态。
    """

    task_id: str
    thread_id: str
    payload: dict
    options: dict
    subtasks: list
    retrieval_results: dict
    matches: Annotated[list, _append]
    analysis: dict
    report: dict
    markdown: str
    iteration_count: int
    no_progress_rounds: int
    token_used: int
    guard: dict
    errors: Annotated[list, _append]
    degraded: bool
    started_at: float
    state: dict


# --------------------------------------------------------------------------- #
# 内部工具
# --------------------------------------------------------------------------- #
def _records_text(payload: dict) -> str:
    records = payload.get("records") or []
    if isinstance(records, str):
        return records[:MAX_EVIDENCE_CHARS]
    parts: list[str] = []
    for item in records:
        if isinstance(item, dict):
            name = item.get("name") or item.get("title") or "材料"
            content = item.get("content") or item.get("value") or ""
            parts.append(f"- 【{name}】{str(content)[:1500]}")
        elif item:
            parts.append(f"- {str(item)[:1500]}")
    text = "\n".join(parts) if parts else "（未提交材料）"
    if payload.get("description"):
        text += f"\n- 【问题描述】{str(payload['description'])[:1500]}"
    return text[:MAX_EVIDENCE_CHARS]


def _clauses_text(clauses: Sequence[Any], limit: int = 5) -> str:
    if not clauses:
        return "（未检索到相关条款）"
    blocks: list[str] = []
    for idx, clause in enumerate(clauses[:limit], start=1):
        blocks.append(
            f"[{idx}] {clause.citation.render()}（相关度 {clause.relevance_score:.3f}）\n"
            f"{clause.content[:MAX_CLAUSE_PREVIEW]}"
        )
    return "\n\n".join(blocks)


def _guard_trigger(state: AgentState, decision, step: str) -> dict:
    logger.warning("节点被守卫终止", extra={"task_id": state.get("task_id"), "step": step, "reason": decision.reason})
    return {
        "guard": decision.to_dict(),
        "errors": [
            {"step": step, "type": decision.mechanism, "message": decision.reason, "fatal": True}
        ],
        "degraded": decision.action == "degrade",
    }


def _usage_tokens() -> int:
    return current_usage().total_tokens


# --------------------------------------------------------------------------- #
# 节点实现
# --------------------------------------------------------------------------- #
async def planning_node(state: AgentState) -> dict:
    """任务拆解（FR-AGT-03）。"""
    from app.core.tracing import trace_span as _trace_span

    with _trace_span("agent.planning", run_type="chain") as _span:
        return _span.done(await _planning_node_impl(state, _span))


async def _planning_node_impl(state: AgentState, _span: Any) -> dict:
    """planning_node 的实现（由包装函数注入追踪 span）。"""
    started = time.perf_counter()
    ctx = get_ctx()
    guard = ctx.guard
    guard.begin_iteration()
    payload = state.get("payload") or {}
    specialty = payload.get("specialty") or "通用"

    prompt = AGENT_PLANNING_USER.format(
        specialty=specialty,
        eval_type=payload.get("eval_type") or "inspection_lot",
        part=payload.get("part") or "未指明",
        project_name=payload.get("project_name") or "未指明",
        records=_records_text(payload),
    )
    tokens_before = _usage_tokens()
    try:
        data, _ = await get_llm().chat_json(
            [ChatMessage("system", AGENT_PLANNING_SYSTEM), ChatMessage("user", prompt)],
            scene="agent_planning",
            max_tokens=2048,
        )
        raw_subtasks = data.get("subtasks") if isinstance(data, dict) else data
    except Exception as exc:  # noqa: BLE001
        logger.warning("任务拆解失败，使用兜底拆解", extra={"error": str(exc)[:200]})
        raw_subtasks = _fallback_subtasks(specialty, payload)
        state.setdefault("errors", []).append(
            {"step": "planning", "type": "llm_failed", "message": str(exc)[:300], "fatal": False}
        )

    subtasks: list[dict] = []
    for idx, item in enumerate(raw_subtasks or []):
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or item.get("title") or f"核查项{idx + 1}").strip()
        if not name:
            continue
        subtasks.append(
            {
                "seq": idx + 1,
                "name": name[:255],
                "criterion": str(item.get("criterion") or "").strip(),
                "required_evidence": str(item.get("required_evidence") or "").strip(),
                "specialty": str(item.get("specialty") or specialty).strip(),
                "query": str(item.get("query") or name).strip(),
            }
        )
    if not subtasks:
        subtasks = _fallback_subtasks(specialty, payload)

    guard.add_tokens(_usage_tokens() - tokens_before)
    ctx.timings["planning"] = int((time.perf_counter() - started) * 1000)
    return {
        "subtasks": subtasks,
        "iteration_count": guard.iteration_count,
        "token_used": guard.token_used,
        "state": {"current_state": EvalState.PLANNING.value, "current_step": "planning"},
    }


def _fallback_subtasks(specialty: str, payload: dict) -> list[dict]:
    """LLM 不可用时的兜底拆解，保证流程不中断（FR-AGT-10 降级）。"""
    part = payload.get("part") or "评估对象"
    return [
        {
            "seq": 1,
            "name": "原材料及构配件进场检验",
            "criterion": "核查原材料与构配件的出厂合格证、进场复验报告是否齐全有效",
            "required_evidence": "出厂合格证、复验报告、见证取样记录",
            "specialty": specialty,
            "query": f"{part} 原材料 进场检验 复验 规定",
        },
        {
            "seq": 2,
            "name": "施工过程质量控制",
            "criterion": "核查施工过程关键参数是否满足规范限值",
            "required_evidence": "施工记录、过程检测记录",
            "specialty": specialty,
            "query": f"{part} 施工过程 质量控制 允许偏差",
        },
        {
            "seq": 3,
            "name": "验收与实体质量",
            "criterion": "核查检验批验收记录与实体检测结果是否合格",
            "required_evidence": "检验批验收记录、实体检测报告",
            "specialty": specialty,
            "query": f"{part} 检验批 验收 合格判定",
        },
    ]


async def retrieval_node(state: AgentState) -> dict:
    """规范检索（FR-AGT-04）：对每个子任务做多阶段检索，命中缓存不重复消耗迭代。"""
    from app.core.tracing import trace_span as _trace_span

    with _trace_span("agent.retrieval", run_type="retriever") as _span:
        return _span.done(await _retrieval_node_impl(state, _span))


async def _retrieval_node_impl(state: AgentState, _span: Any) -> dict:
    """retrieval_node 的实现（由包装函数注入追踪 span）。"""
    started = time.perf_counter()
    ctx = get_ctx()
    guard = ctx.guard
    guard.begin_iteration()
    subtasks = state.get("subtasks") or []
    session = ctx.session
    pipeline = ctx.pipeline
    payload = state.get("payload") or {}
    kb_ids = (state.get("options") or {}).get("kb_ids") or payload.get("kb_ids")

    tokens_before = _usage_tokens()
    results: dict[str, Any] = {}
    degraded = bool(state.get("degraded"))

    async def one(subtask: dict) -> tuple[str, dict]:
        query = subtask.get("query") or subtask.get("name") or ""
        cached = guard.dedup.get("retrieval", query)
        if cached is not None:
            return query, cached
        # 经工具注册表调用：统一做权限校验、参数校验与审计（见 agent/tools.py）。
        # 不经 pipeline 直接调用，避免绕过知识库授权闸门。
        from app.agent.tools import get_registry

        result, record = await get_registry().invoke(
            "kb_retrieve",
            ctx,
            query=query,
            kb_ids=list(kb_ids) if kb_ids else None,
            specialty=subtask.get("specialty"),
            top_k=5,
        )
        ctx.tool_calls.append(record)
        if result is None:
            logger.warning(
                "子任务检索被拒绝或失败",
                extra={"query": query[:60], "reason": record.reason[:200]},
            )
            return query, {
                "query": query,
                "error": record.reason,
                "clauses": [],
                "no_evidence": True,
                "degraded": True,
            }
        payload_out = {"query": query, **result}
        guard.dedup.put("retrieval", query, payload_out)
        return query, payload_out

    pairs = await asyncio.gather(*[one(st) for st in subtasks])
    for key, value in pairs:
        results[key] = value
        if value.get("degraded") or value.get("error"):
            degraded = True

    all_clauses = [
        clause
        for value in results.values()
        for clause in (value.get("clauses") or [])
    ]
    new_clause_count = len({c.get("chunk_id") for c in all_clauses})
    matched_count = len(state.get("matches") or [])

    guard.add_tokens(_usage_tokens() - tokens_before)
    decision = guard.check(
        ProgressSnapshot(
            evidence_count=len(all_clauses),
            matched_count=matched_count,
            top_score=max((c.get("relevance_score") or 0 for c in all_clauses), default=0.0),
            new_clause_count=new_clause_count,
            round_index=guard.iteration_count,
        )
    )
    if decision.stop:
        return _guard_trigger(state, decision, "retrieval")

    ctx.timings["retrieval"] = int((time.perf_counter() - started) * 1000)
    return {
        "retrieval_results": results,
        "iteration_count": guard.iteration_count,
        "no_progress_rounds": guard.no_progress_rounds,
        "token_used": guard.token_used,
        "degraded": degraded,
        "state": {"current_state": EvalState.RETRIEVING.value, "current_step": "retrieval"},
    }


async def matching_node(state: AgentState) -> dict:
    """条款匹配（FR-AGT-05）：证据 vs 条款逐条比对。"""
    from app.core.tracing import trace_span as _trace_span

    with _trace_span("agent.clause_matching", run_type="chain") as _span:
        return _span.done(await _matching_node_impl(state, _span))


async def _matching_node_impl(state: AgentState, _span: Any) -> dict:
    """matching_node 的实现（由包装函数注入追踪 span）。"""
    started = time.perf_counter()
    ctx = get_ctx()
    guard = ctx.guard
    guard.begin_iteration()
    subtasks = state.get("subtasks") or []
    retrieval_results = state.get("retrieval_results") or {}
    payload = state.get("payload") or {}
    evidence_text = _records_text(payload)

    tokens_before = _usage_tokens()
    matches: list[dict] = []

    async def one(subtask: dict) -> Optional[dict]:
        key = subtask.get("query") or subtask.get("name") or ""
        found = retrieval_results.get(key) or {}
        clauses = found.get("clauses") or []
        if not clauses:
            return {
                "subtask_seq": subtask.get("seq"),
                "subtask_name": subtask.get("name"),
                "verdict": Verdict.INSUFFICIENT_EVIDENCE.value,
                "confidence": 0.0,
                "clause_no": None,
                "spec_code": None,
                "relevance_score": 0.0,
                "evidence": "",
                "reasoning": "未检索到可支撑判定的规范条款，无法作出符合性判断。",
                "risk_level": "medium",
                "remediation": "补充该核查项对应的现行规范依据后重新评估。",
                "citation": {},
            }
        prompt = AGENT_MATCHING_USER.format(
            subtask_name=subtask.get("name") or "",
            criterion=subtask.get("criterion") or "",
            required_evidence=subtask.get("required_evidence") or "",
            evidence_text=evidence_text,
            clauses=_clauses_text(_ClauseView.wrap(clauses)),
        )
        try:
            data, _ = await get_llm().chat_json(
                [ChatMessage("system", AGENT_MATCHING_SYSTEM), ChatMessage("user", prompt)],
                scene="agent_matching",
                max_tokens=1024,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("条款匹配失败", extra={"subtask": subtask.get("name"), "error": str(exc)[:200]})
            data = {
                "verdict": Verdict.INSUFFICIENT_EVIDENCE.value,
                "confidence": 0.0,
                "evidence": "",
                "reasoning": f"模型调用失败，判定降级为证据不足：{str(exc)[:120]}",
                "risk_level": "medium",
                "remediation": "人工复核该核查项。",
            }
        data = data if isinstance(data, dict) else {}
        top = clauses[0]
        clause_no = str(data.get("clause_no") or top.get("clause_no") or "") or None
        # 模型给出的条款号必须在候选条款内，否则回退到重排第一条，避免幻觉引用
        candidate_nos = {c.get("clause_no") for c in clauses if c.get("clause_no")}
        if clause_no not in candidate_nos:
            clause_no = top.get("clause_no")
        chosen = next((c for c in clauses if c.get("clause_no") == clause_no), top)
        verdict = str(data.get("verdict") or Verdict.INSUFFICIENT_EVIDENCE.value).lower()
        if verdict not in {v.value for v in Verdict}:
            verdict = Verdict.INSUFFICIENT_EVIDENCE.value
        return {
            "subtask_seq": subtask.get("seq"),
            "subtask_name": subtask.get("name"),
            "clause_no": clause_no,
            "spec_code": chosen.get("spec_code"),
            "verdict": verdict,
            "confidence": _clamp(data.get("confidence"), 0.0, 1.0),
            "relevance_score": chosen.get("relevance_score") or 0.0,
            "evidence": str(data.get("evidence") or "")[:2000],
            "reasoning": str(data.get("reasoning") or "")[:2000],
            "risk_level": _risk(data.get("risk_level"), verdict),
            "remediation": str(data.get("remediation") or "")[:1000],
            "citation": chosen.get("citation") or {},
        }

    results = await asyncio.gather(*[one(st) for st in subtasks])
    matches = [m for m in results if m]

    guard.add_tokens(_usage_tokens() - tokens_before)
    new_match_count = count_new_items(state.get("matches") or [], matches)
    decision = guard.check(
        ProgressSnapshot(
            evidence_count=len(matches),
            matched_count=len(matches),
            top_score=max((m.get("relevance_score") or 0 for m in matches), default=0.0),
            new_clause_count=new_match_count,
            round_index=guard.iteration_count,
        )
    )
    if decision.stop:
        return _guard_trigger(state, decision, "clause_matching")

    ctx.timings["clause_matching"] = int((time.perf_counter() - started) * 1000)
    return {
        "matches": matches,
        "iteration_count": guard.iteration_count,
        "no_progress_rounds": guard.no_progress_rounds,
        "token_used": guard.token_used,
        "state": {"current_state": EvalState.MATCHING.value, "current_step": "clause_matching"},
    }


async def analysis_node(state: AgentState) -> dict:
    """结果分析（FR-AGT-06）：汇总不符合项、原因与整改建议。"""
    from app.core.tracing import trace_span as _trace_span

    with _trace_span("agent.analysis", run_type="chain") as _span:
        return _span.done(await _analysis_node_impl(state, _span))


async def _analysis_node_impl(state: AgentState, _span: Any) -> dict:
    """analysis_node 的实现（由包装函数注入追踪 span）。"""
    started = time.perf_counter()
    ctx = get_ctx()
    guard = ctx.guard
    guard.begin_iteration()
    matches = state.get("matches") or []
    payload = state.get("payload") or {}
    tokens_before = _usage_tokens()

    compact = [
        {
            "subtask": m.get("subtask_name"),
            "clause_no": m.get("clause_no"),
            "verdict": report_renderer.verdict_label(m.get("verdict")),
            "evidence": (m.get("evidence") or "")[:400],
            "reasoning": (m.get("reasoning") or "")[:400],
        }
        for m in matches
    ]
    prompt = AGENT_ANALYSIS_USER.format(
        part=payload.get("part") or "未指明",
        specialty=payload.get("specialty") or "通用",
        matches=json.dumps(compact, ensure_ascii=False, indent=2)[:6000],
    )
    try:
        data, _ = await get_llm().chat_json(
            [ChatMessage("system", AGENT_ANALYSIS_SYSTEM), ChatMessage("user", prompt)],
            scene="agent_analysis",
            max_tokens=2048,
        )
        analysis = data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("结果分析失败，使用规则汇总", extra={"error": str(exc)[:200]})
        analysis = _fallback_analysis(matches)
        state.setdefault("errors", []).append(
            {"step": "analysis", "type": "llm_failed", "message": str(exc)[:300], "fatal": False}
        )

    analysis.setdefault("findings", [])
    analysis.setdefault("risk_level", report_renderer.risk_from_matches(matches))
    analysis.setdefault("summary", "")
    analysis.setdefault("conclusion", "")

    guard.add_tokens(_usage_tokens() - tokens_before)
    decision = guard.check()
    if decision.stop:
        return _guard_trigger(state, decision, "analysis")

    ctx.timings["analysis"] = int((time.perf_counter() - started) * 1000)
    return {
        "analysis": analysis,
        "iteration_count": guard.iteration_count,
        "token_used": guard.token_used,
        "state": {"current_state": EvalState.ANALYZING.value, "current_step": "analysis"},
    }


def _fallback_analysis(matches: Sequence[dict]) -> dict:
    findings = [
        {
            "clause_no": m.get("clause_no"),
            "problem": f"{m.get('subtask_name')}：{m.get('reasoning') or '不满足条款要求'}",
            "cause": "根据提交材料比对发现偏差，具体原因需现场核实。",
            "risk_level": m.get("risk_level") or "medium",
            "remediation": m.get("remediation") or "按规范要求整改后重新报验。",
        }
        for m in matches
        if m.get("verdict") in (Verdict.NON_COMPLIANT.value, Verdict.PARTIAL.value, Verdict.INSUFFICIENT_EVIDENCE.value)
    ]
    return {
        "summary": f"本次评估共核查 {len(matches)} 项，其中不符合或部分符合 {len(findings)} 项。",
        "risk_level": report_renderer.risk_from_matches(matches),
        "findings": findings,
        "conclusion": f"总体判定：{report_renderer.verdict_label(report_renderer.overall_verdict_from_matches(matches))}。",
    }


async def report_node(state: AgentState) -> dict:
    """报告生成（FR-AGT-07）。"""
    from app.core.tracing import trace_span as _trace_span

    with _trace_span("agent.report_generation", run_type="chain") as _span:
        return _span.done(await _report_node_impl(state, _span))


async def _report_node_impl(state: AgentState, _span: Any) -> dict:
    """report_node 的实现（由包装函数注入追踪 span）。"""
    started = time.perf_counter()
    ctx = get_ctx()
    guard = ctx.guard
    guard.begin_iteration()
    matches = state.get("matches") or []
    analysis = state.get("analysis") or {}
    payload = state.get("payload") or {}
    tokens_before = _usage_tokens()

    basis = _collect_basis(matches)
    prompt = AGENT_REPORT_USER.format(
        part=payload.get("part") or "未指明",
        specialty=payload.get("specialty") or "通用",
        project_name=payload.get("project_name") or "未指明",
        eval_time=utcnow().strftime("%Y-%m-%d %H:%M"),
        basis=json.dumps(basis, ensure_ascii=False, indent=2)[:3000],
        matches=json.dumps(
            [
                {
                    "subtask": m.get("subtask_name"),
                    "clause_no": m.get("clause_no"),
                    "verdict": report_renderer.verdict_label(m.get("verdict")),
                    "reasoning": (m.get("reasoning") or "")[:300],
                }
                for m in matches
            ],
            ensure_ascii=False,
        )[:6000],
        analysis=json.dumps(analysis, ensure_ascii=False)[:3000],
    )
    try:
        data, _ = await get_llm().chat_json(
            [ChatMessage("system", AGENT_REPORT_SYSTEM), ChatMessage("user", prompt)],
            scene="agent_report",
            max_tokens=3072,
        )
        report = data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        logger.warning("报告生成失败，使用结构化兜底", extra={"error": str(exc)[:200]})
        report = {}
        state.setdefault("errors", []).append(
            {"step": "report_generation", "type": "llm_failed", "message": str(exc)[:300], "fatal": False}
        )

    # 结论口径以确定性推导为准，避免模型拔高或淡化（SRS 2.6 C3）
    report["overall_verdict"] = report_renderer.overall_verdict_from_matches(matches)
    report["risk_level"] = report_renderer.risk_from_matches(matches)
    report.setdefault("title", f"{payload.get('part') or '工程'}质量评估报告")
    report.setdefault("overview", f"依据现行规范对{payload.get('part') or '评估对象'}进行智能初评。")
    report.setdefault("conclusion", analysis.get("conclusion") or "")
    report["analysis"] = analysis

    task_meta = {
        "project_name": payload.get("project_name"),
        "specialty": payload.get("specialty"),
        "part": payload.get("part"),
        "eval_type": payload.get("eval_type"),
        "eval_time": utcnow().strftime("%Y-%m-%d %H:%M"),
        "task_id": state.get("task_id"),
    }
    markdown = report_renderer.render_markdown(
        report, task_meta=task_meta, matches=matches, evidence_basis=basis
    )

    guard.add_tokens(_usage_tokens() - tokens_before)
    decision = guard.check()
    if decision.stop:
        return _guard_trigger(state, decision, "report_generation")

    ctx.timings["report_generation"] = int((time.perf_counter() - started) * 1000)
    return {
        "report": report,
        "markdown": markdown,
        "iteration_count": guard.iteration_count,
        "token_used": guard.token_used,
        "state": {"current_state": EvalState.REPORTING.value, "current_step": "report_generation"},
    }


def _collect_basis(matches: Sequence[dict]) -> list[dict]:
    basis: list[dict] = []
    seen: set[str] = set()
    for match in matches:
        citation = match.get("citation") or {}
        key = f"{citation.get('spec_code')}|{citation.get('clause_no')}|{citation.get('chunk_id')}"
        if key in seen:
            continue
        seen.add(key)
        if not citation:
            continue
        basis.append(
            {
                "spec_code": citation.get("spec_code"),
                "spec_name": citation.get("spec_name"),
                "clause_no": citation.get("clause_no"),
                "location": _location(citation),
                "chunk_id": citation.get("chunk_id"),
            }
        )
    return basis


def _location(citation: dict) -> str:
    parts = []
    if citation.get("chapter_path"):
        parts.append(str(citation["chapter_path"]))
    if citation.get("page_no"):
        parts.append(f"P{citation['page_no']}")
    return " / ".join(parts) or "-"


def finalize_node(state: AgentState) -> dict:
    """收敛节点：根据守卫结果与降级情况确定终态（SRS 4.1）。"""
    guard = get_ctx().guard
    decision = state.get("guard") or {}
    has_report = bool(state.get("report"))
    matches = state.get("matches") or []

    if decision.get("stop"):
        action = decision.get("action")
        if action == "degrade":
            target = EvalState.DEGRADED
        elif action == "need_human":
            target = EvalState.NEED_HUMAN
        else:
            target = EvalState.FAILED
    elif state.get("degraded"):
        target = EvalState.DEGRADED
    elif any(m.get("verdict") == Verdict.INSUFFICIENT_EVIDENCE.value for m in matches):
        target = EvalState.NEED_HUMAN
    elif _report_needs_human(state):
        # 「不符合」或「高风险」的结论必须经人工确认后才能成为正式报告
        # （FR-JDG-05「不直接出具正式报告」；与「人工复核裁定」的签发语义一致）。
        #
        # 此前只按「证据不足」触发转人工，于是出现过这样的漏洞：
        # 报告结论为「不符合」、风险「高」，但因**未执行 LLM-as-Judge**
        # （没有分数，低分闸门无从触发），任务直接到达 COMPLETED ——
        # 一份未签发的「不符合」报告就这样等同正式出具了。
        target = EvalState.NEED_HUMAN
    elif has_report:
        target = EvalState.COMPLETED
    else:
        target = EvalState.FAILED

    return {
        "state": {
            "current_state": target.value,
            "guard_reason": decision.get("reason"),
            "summary": guard.summary(),
        }
    }


def _report_needs_human(state: dict) -> bool:
    """报告是否必须转人工复核。

    判据取「结论」与「风险」两个维度中任一项为负面：

    - 结论为 ``non_compliant``（不符合）；
    - 风险等级为 ``high``。

    注意**不含** ``partial``（部分符合）：那是常见的中间结论，
    若也强制复核会让绝大多数任务都转人工，反而失去拦截意义。
    ``insufficient_evidence`` 已在上游单独处理。
    """
    report = state.get("report") or {}
    if not isinstance(report, dict):
        return False
    verdict = str(report.get("overall_verdict") or "").lower()
    risk = str(report.get("risk_level") or "").lower()
    return verdict == "non_compliant" or risk == "high"


# --------------------------------------------------------------------------- #
# 图构建
# --------------------------------------------------------------------------- #
class _ClauseView:
    """把检索结果字典包装成模板所需的属性访问对象。"""

    def __init__(self, data: dict) -> None:
        self._data = data

    @property
    def citation(self):
        citation = self._data.get("citation") or {}

        class _C:
            def render(self_inner) -> str:  # noqa: N805
                spec = " ".join(
                    p for p in (citation.get("spec_code"), citation.get("spec_name")) if p
                )
                tail = []
                if citation.get("chapter_path"):
                    tail.append(f"章节：{citation['chapter_path']}")
                if citation.get("clause_no"):
                    tail.append(f"条款：{citation['clause_no']}")
                if citation.get("page_no"):
                    tail.append(f"页码：P{citation['page_no']}")
                return f"{spec}（{'；'.join(tail)}）" if tail else (spec or "-")

        return _C()

    @property
    def content(self) -> str:
        return str(self._data.get("content") or "")

    @property
    def relevance_score(self) -> float:
        return float(self._data.get("relevance_score") or 0.0)

    @classmethod
    def wrap(cls, items: Sequence[dict]) -> list["_ClauseView"]:
        return [cls(item) for item in items]


def build_agent_graph(checkpointer: Any = None):
    """构建五阶段工作流（线性主链 + 守卫跳转 finalize）。"""
    graph = StateGraph(AgentState)
    graph.add_node("planning", planning_node)
    graph.add_node("retrieval", retrieval_node)
    graph.add_node("clause_matching", matching_node)
    graph.add_node("analysis", analysis_node)
    graph.add_node("report_generation", report_node)
    graph.add_node("finalize", finalize_node)

    graph.add_edge(START, "planning")
    graph.add_edge("planning", "retrieval")
    graph.add_edge("retrieval", "clause_matching")
    graph.add_edge("clause_matching", "analysis")
    graph.add_edge("analysis", "report_generation")
    graph.add_edge("report_generation", "finalize")
    graph.add_edge("finalize", END)

    return graph.compile(checkpointer=checkpointer or InMemorySaver())


_compiled: dict[str, Any] = {}


def get_agent_graph(checkpointer: Any = None, cache_key: str = "default"):
    if cache_key not in _compiled:
        _compiled[cache_key] = build_agent_graph(checkpointer)
    return _compiled[cache_key]


def reset_agent_graph() -> None:
    _compiled.clear()


def _clamp(value: Any, low: float, high: float) -> float:
    try:
        num = float(value)
    except (TypeError, ValueError):
        return low
    return max(low, min(high, num))


def _risk(value: Any, verdict: str) -> str:
    level = str(value or "").lower()
    if level in {"high", "medium", "low"}:
        return level
    if verdict == Verdict.NON_COMPLIANT.value:
        return "high"
    if verdict == Verdict.PARTIAL.value:
        return "medium"
    return "low"


__all__ = [
    "AgentState",
    "build_agent_graph",
    "get_agent_graph",
    "reset_agent_graph",
    "planning_node",
    "retrieval_node",
    "matching_node",
    "analysis_node",
    "report_node",
    "finalize_node",
]
