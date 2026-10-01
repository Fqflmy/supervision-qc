# -*- coding: utf-8 -*-
"""防死循环收敛守卫测试（SRS FR-AGT-09 / 4.3 七项机制）。"""
from __future__ import annotations

import time

import pytest

from app.agent.guards import (
    CallDeduplicator,
    ConvergenceGuard,
    GuardPolicy,
    ProgressSnapshot,
    count_new_items,
)


def make_policy(**overrides) -> GuardPolicy:
    base = {
        "max_iterations": 3,
        "no_progress_limit": 2,
        "tool_timeout_seconds": 30.0,
        "task_timeout_seconds": 900.0,
        "token_budget": 1000,
    }
    base.update(overrides)
    return GuardPolicy(**base)


def test_max_iterations_triggers_degrade():
    """机制 1：迭代次数超上限应终止并降级。"""
    guard = ConvergenceGuard(make_policy(max_iterations=2))
    assert guard.begin_iteration() == 1
    assert not guard.check().stop
    guard.begin_iteration()
    assert not guard.check().stop
    guard.begin_iteration()  # 第 3 次，超过上限 2
    decision = guard.check()
    assert decision.stop
    assert decision.mechanism == "max_iterations"
    assert decision.action == "degrade"


def test_no_progress_detection_counts_rounds():
    """机制 2：连续无新增信息达到阈值即终止转人工。"""
    guard = ConvergenceGuard(make_policy(no_progress_limit=2))
    steady = ProgressSnapshot(evidence_count=3, matched_count=2, top_score=0.5, new_clause_count=3, round_index=1)
    assert not guard.check(steady).stop
    # 第二轮完全无进展
    assert not guard.check(steady).stop
    assert guard.no_progress_rounds == 1
    # 第三轮仍无进展 → 达到阈值 2
    decision = guard.check(steady)
    assert decision.stop
    assert decision.mechanism == "no_progress"
    assert decision.action == "need_human"


def test_progress_resets_no_progress_counter():
    """有新增信息时无进展计数必须清零，避免误杀。"""
    guard = ConvergenceGuard(make_policy(no_progress_limit=2))
    guard.check(ProgressSnapshot(new_clause_count=1, matched_count=1, top_score=0.1))
    guard.check(ProgressSnapshot(new_clause_count=1, matched_count=1, top_score=0.1))
    assert guard.no_progress_rounds == 1
    improved = ProgressSnapshot(new_clause_count=4, matched_count=3, top_score=0.6)
    decision = guard.check(improved)
    assert not decision.stop
    assert guard.no_progress_rounds == 0


def test_top_score_improvement_counts_as_progress():
    """top 分数提升超过阈值也算有进展。"""
    guard = ConvergenceGuard(make_policy(no_progress_limit=1, min_progress_delta=0.01))
    guard.check(ProgressSnapshot(new_clause_count=1, top_score=0.30))
    decision = guard.check(ProgressSnapshot(new_clause_count=1, top_score=0.55))
    assert not decision.stop
    assert guard.no_progress_rounds == 0


def test_token_budget_triggers_degrade():
    """机制 7：Token 预算超限应降级收敛。"""
    guard = ConvergenceGuard(make_policy(token_budget=100))
    guard.add_tokens(150)
    decision = guard.check()
    assert decision.stop
    assert decision.mechanism == "token_budget"
    assert decision.action == "degrade"


def test_task_timeout_triggers_need_human():
    """机制 6：总耗时超限应转人工而非静默失败。"""
    guard = ConvergenceGuard(make_policy(task_timeout_seconds=0.01))
    time.sleep(0.02)
    decision = guard.check()
    assert decision.stop
    assert decision.mechanism == "task_timeout"
    assert decision.action == "need_human"


def test_retry_limit_and_backoff():
    """机制 4：单步重试上限与指数退避。"""
    guard = ConvergenceGuard(make_policy())
    guard.policy.step_max_retries = 3
    assert guard.should_retry(1, RuntimeError("boom"))
    assert guard.should_retry(2, RuntimeError("boom"))
    assert not guard.should_retry(3, RuntimeError("boom"))
    assert guard.backoff_seconds(1) == pytest.approx(1.0)
    assert guard.backoff_seconds(2) == pytest.approx(2.0)
    assert guard.backoff_seconds(3) == pytest.approx(4.0)


def test_deduplicator_reuses_cached_result():
    """机制 5：相同工具+参数指纹命中缓存，不重复调用。"""
    dedup = CallDeduplicator()
    assert dedup.get("retrieval", "混凝土入模温度") is None
    dedup.put("retrieval", "混凝土入模温度", {"clauses": 3})
    assert dedup.get("retrieval", "混凝土入模温度") == {"clauses": 3}
    assert dedup.hits == 1
    # 参数不同则视为不同调用
    assert dedup.get("retrieval", "脚手架连墙件") is None


def test_policy_from_options_casts_and_ignores_bad_values():
    """任务级可覆盖守卫参数，非法值回退默认。"""
    policy = GuardPolicy.from_options(
        {"max_iterations": "5", "no_progress_limit": 1, "task_timeout_seconds": "120", "token_budget": "abc"}
    )
    assert policy.max_iterations == 5
    assert policy.no_progress_limit == 1
    assert policy.task_timeout_seconds == pytest.approx(120.0)
    assert policy.token_budget == GuardPolicy().token_budget  # 非法值未生效


def test_guard_summary_reports_limits_and_stops():
    guard = ConvergenceGuard(make_policy(max_iterations=1))
    guard.begin_iteration()
    guard.begin_iteration()
    guard.check()
    summary = guard.summary()
    assert summary["iterations"] == 2
    assert summary["limits"]["max_iterations"] == 1
    assert summary["stops"] and summary["stops"][0]["mechanism"] == "max_iterations"


def test_count_new_items_by_key():
    previous = [{"chunk_id": 1}, {"chunk_id": 2}]
    current = [{"chunk_id": 2}, {"chunk_id": 3}, {"chunk_id": 4}]
    assert count_new_items(previous, current) == 2
