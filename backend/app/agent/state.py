# -*- coding: utf-8 -*-
"""Agent 状态机（SRS 4.1/4.2）。

- 校验状态迁移合法性（非法迁移直接抛错，避免脏状态写入）；
- 维护迭代计数、无进展轮次、检查点列表、异常状态；
- 与 LangGraph Checkpointer 配合实现断点续跑（FR-AGT-08）。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from app.constants import STATE_TRANSITIONS, TERMINAL_STATES, EvalState
from app.core.errors import AppError
from app.core.logging_conf import get_logger

logger = get_logger(__name__)


class InvalidTransitionError(AppError):
    code_key = "CONFLICT"
    http_status = 409
    message = "非法的状态迁移"


@dataclass
class TaskState:
    """Agent 任务状态（SRS 4.2 状态字段）。"""

    task_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    thread_id: str = ""
    current_state: EvalState = EvalState.PENDING
    current_step: Optional[str] = None
    completed_steps: list[dict] = field(default_factory=list)
    retrieval_results: dict = field(default_factory=dict)
    matched_clauses: list[dict] = field(default_factory=list)
    iteration_count: int = 0
    no_progress_rounds: int = 0
    error_state: Optional[dict] = None
    checkpoints: list[str] = field(default_factory=list)
    token_used: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    guard_reason: Optional[str] = None

    def to_snapshot(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "thread_id": self.thread_id,
            "current_state": self.current_state.value,
            "current_step": self.current_step,
            "completed_steps": list(self.completed_steps),
            "iteration_count": self.iteration_count,
            "no_progress_rounds": self.no_progress_rounds,
            "error_state": self.error_state,
            "checkpoints": list(self.checkpoints),
            "token_used": self.token_used,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "guard_reason": self.guard_reason,
            "matched_count": len(self.matched_clauses),
            "retrieval_keys": list(self.retrieval_results.keys()),
        }


class StateMachine:
    """状态迁移控制器。"""

    def __init__(self, state: TaskState) -> None:
        self.state = state

    @staticmethod
    def can_transition(current: EvalState, target: EvalState) -> bool:
        if current == target:
            return True
        return target in STATE_TRANSITIONS.get(current, set())

    def transition(
        self,
        target: EvalState,
        *,
        step: Optional[str] = None,
        enforce: bool = True,
        reason: Optional[str] = None,
    ) -> EvalState:
        current = self.state.current_state
        if enforce and not self.can_transition(current, target):
            raise InvalidTransitionError(
                f"不允许从 {current.value} 迁移到 {target.value}",
                details={"current": current.value, "target": target.value, "allowed": sorted(
                    s.value for s in STATE_TRANSITIONS.get(current, set())
                )},
            )
        self.state.current_state = target
        if step is not None:
            self.state.current_step = step
        if target in TERMINAL_STATES:
            from app.db import utcnow

            self.state.finished_at = utcnow().isoformat()
        if reason:
            self.state.guard_reason = reason
        logger.info(
            "状态迁移",
            extra={"task_id": self.state.task_id, "from": current.value, "to": target.value, "step": step},
        )
        return target

    def mark_step_done(
        self, step: str, *, duration_ms: Optional[int] = None, token_used: int = 0, digest: Optional[dict] = None
    ) -> None:
        self.state.completed_steps.append(
            {
                "step": step,
                "duration_ms": duration_ms,
                "token_used": token_used,
                "digest": digest or {},
            }
        )
        self.state.token_used += token_used

    def add_checkpoint(self, checkpoint_id: str) -> None:
        self.state.checkpoints.append(checkpoint_id)

    def record_error(self, *, error_type: str, message: str, retry_count: int = 0) -> None:
        self.state.error_state = {
            "type": error_type,
            "message": message[:1000],
            "retry_count": retry_count,
        }


__all__ = ["TaskState", "StateMachine", "InvalidTransitionError"]
