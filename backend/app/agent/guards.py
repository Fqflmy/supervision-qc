# -*- coding: utf-8 -*-
"""防死循环与收敛守卫（SRS FR-AGT-09 / 4.3）。

七项机制：
1. 最大迭代次数（默认 12）；
2. 无进展检测（连续 2 轮无新增有效信息）；
3. 工具调用超时（默认 30s）；
4. 单步重试上限（≤3 次，指数退避）；
5. 重复调用去重（相同工具+参数指纹直接复用缓存，不计数迭代）；
6. 总耗时上限（默认 15 分钟）；
7. Token 预算（默认 200K）。
"""
from __future__ import annotations

import hashlib
import json
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from app.config import settings
from app.core.logging_conf import get_logger

logger = get_logger(__name__)


@dataclass
class GuardDecision:
    """守卫裁决。"""

    stop: bool = False
    action: str = "continue"  # continue | degrade | fail | need_human
    reason: Optional[str] = None
    mechanism: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {"stop": self.stop, "action": self.action, "reason": self.reason, "mechanism": self.mechanism}


@dataclass
class ProgressSnapshot:
    """单轮进展快照，用于无进展检测。"""

    evidence_count: int = 0
    matched_count: int = 0
    top_score: float = 0.0
    new_clause_count: int = 0
    round_index: int = 0


@dataclass
class GuardPolicy:
    """收敛策略（可由任务 options 覆盖）。"""

    max_iterations: int = field(default_factory=lambda: settings.agent_max_iterations)
    no_progress_limit: int = field(default_factory=lambda: settings.agent_no_progress_limit)
    tool_timeout_seconds: float = field(default_factory=lambda: settings.agent_tool_timeout_seconds)
    step_max_retries: int = field(default_factory=lambda: settings.agent_step_max_retries)
    task_timeout_seconds: float = field(default_factory=lambda: settings.agent_task_timeout_seconds)
    token_budget: int = field(default_factory=lambda: settings.agent_token_budget)
    min_progress_delta: float = 0.01

    @classmethod
    def from_options(cls, options: Optional[dict]) -> "GuardPolicy":
        options = options or {}
        policy = cls()
        mapping = {
            "max_iterations": ("max_iterations", int),
            "no_progress_limit": ("no_progress_limit", int),
            "tool_timeout_seconds": ("tool_timeout_seconds", float),
            "task_timeout_seconds": ("task_timeout_seconds", float),
            "token_budget": ("token_budget", int),
        }
        for key, (attr, caster) in mapping.items():
            if key in options and options[key] is not None:
                try:
                    setattr(policy, attr, caster(options[key]))
                except (TypeError, ValueError):
                    logger.warning("守卫参数非法，使用默认值", extra={"key": key, "value": options[key]})
        return policy


class CallDeduplicator:
    """重复调用去重：相同工具+参数指纹命中缓存则不消耗迭代次数。"""

    def __init__(self) -> None:
        self._cache: dict[str, Any] = {}
        self._hits = 0

    @staticmethod
    def fingerprint(tool: str, params: Any) -> str:
        payload = json.dumps({"tool": tool, "params": params}, ensure_ascii=False, sort_keys=True, default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]

    def get(self, tool: str, params: Any) -> Optional[Any]:
        key = self.fingerprint(tool, params)
        if key in self._cache:
            self._hits += 1
            return self._cache[key]
        return None

    def put(self, tool: str, params: Any, value: Any) -> None:
        self._cache[self.fingerprint(tool, params)] = value

    @property
    def hits(self) -> int:
        return self._hits


class ConvergenceGuard:
    """收敛守卫：每轮调用 check()，返回是否应当终止。"""

    def __init__(self, policy: Optional[GuardPolicy] = None) -> None:
        self.policy = policy or GuardPolicy()
        self.started_at = time.monotonic()
        self.iteration_count = 0
        self.no_progress_rounds = 0
        self.token_used = 0
        self.last_snapshot: Optional[ProgressSnapshot] = None
        self.dedup = CallDeduplicator()
        self.stop_history: list[dict] = []

    # ------------------------------------------------------------------ #
    def begin_iteration(self) -> int:
        self.iteration_count += 1
        return self.iteration_count

    def add_tokens(self, tokens: int) -> None:
        self.token_used += max(0, int(tokens))

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    # ------------------------------------------------------------------ #
    def check(self, snapshot: Optional[ProgressSnapshot] = None) -> GuardDecision:
        """按优先级依次检查全部机制，返回首个命中的裁决。"""
        # 机制 6：总耗时上限
        if self.elapsed_seconds > self.policy.task_timeout_seconds:
            return self._stop(
                "task_timeout",
                f"任务总耗时 {self.elapsed_seconds:.0f}s 超过上限 {self.policy.task_timeout_seconds:.0f}s",
                action="need_human",
            )
        # 机制 7：Token 预算
        if self.token_used > self.policy.token_budget:
            return self._stop(
                "token_budget",
                f"Token 消耗 {self.token_used} 超过预算 {self.policy.token_budget}",
                action="degrade",
            )
        # 机制 1：最大迭代次数
        if self.iteration_count > self.policy.max_iterations:
            return self._stop(
                "max_iterations",
                f"迭代次数 {self.iteration_count} 超过上限 {self.policy.max_iterations}",
                action="degrade",
            )
        # 机制 2：无进展检测
        if snapshot is not None:
            if self.last_snapshot is not None and not self._has_progress(self.last_snapshot, snapshot):
                self.no_progress_rounds += 1
            else:
                self.no_progress_rounds = 0
            self.last_snapshot = snapshot
            if self.no_progress_rounds >= self.policy.no_progress_limit:
                return self._stop(
                    "no_progress",
                    f"连续 {self.no_progress_rounds} 轮无新增有效信息",
                    action="need_human",
                )
        return GuardDecision()

    def _has_progress(self, previous: ProgressSnapshot, current: ProgressSnapshot) -> bool:
        """进展判定：新增条款数、匹配数、top 分数提升任一有增益即视为有进展。"""
        if current.new_clause_count > previous.new_clause_count:
            return True
        if current.matched_count > previous.matched_count:
            return True
        if current.evidence_count > previous.evidence_count:
            return True
        if current.top_score - previous.top_score > self.policy.min_progress_delta:
            return True
        return False

    def _stop(self, mechanism: str, reason: str, *, action: str) -> GuardDecision:
        decision = GuardDecision(stop=True, action=action, reason=reason, mechanism=mechanism)
        self.stop_history.append(decision.to_dict())
        logger.warning("收敛守卫触发", extra={"mechanism": mechanism, "reason": reason, "action": action})
        return decision

    # ------------------------------------------------------------------ #
    def should_retry(self, attempt: int, error: Exception) -> bool:
        """机制 4：重试上限。attempt 从 1 开始计数。"""
        if attempt >= self.policy.step_max_retries:
            logger.warning(
                "重试次数耗尽",
                extra={"attempt": attempt, "max": self.policy.step_max_retries, "error": str(error)[:200]},
            )
            return False
        return True

    @staticmethod
    def backoff_seconds(attempt: int) -> float:
        """指数退避 1s / 2s / 4s（SRS 4.3）。"""
        return settings.llm_retry_backoff_seconds * (2 ** max(0, attempt - 1))

    def summary(self) -> dict[str, Any]:
        return {
            "iterations": self.iteration_count,
            "no_progress_rounds": self.no_progress_rounds,
            "token_used": self.token_used,
            "elapsed_seconds": round(self.elapsed_seconds, 2),
            "dedup_hits": self.dedup.hits,
            "stops": list(self.stop_history),
            "limits": {
                "max_iterations": self.policy.max_iterations,
                "no_progress_limit": self.policy.no_progress_limit,
                "task_timeout_seconds": self.policy.task_timeout_seconds,
                "token_budget": self.policy.token_budget,
            },
        }


def count_new_items(previous: Sequence[Any], current: Sequence[Any]) -> int:
    """计算新增条目数（用于进展快照）。"""
    previous_keys = {_key(item) for item in previous}
    return sum(1 for item in current if _key(item) not in previous_keys)


def _key(item: Any) -> str:
    if isinstance(item, dict):
        for field_name in ("chunk_id", "clause_no", "id", "name"):
            if item.get(field_name) is not None:
                return str(item[field_name])
        return json.dumps(item, ensure_ascii=False, sort_keys=True, default=str)[:120]
    return str(item)


__all__ = [
    "GuardDecision",
    "GuardPolicy",
    "ProgressSnapshot",
    "ConvergenceGuard",
    "CallDeduplicator",
    "count_new_items",
]
