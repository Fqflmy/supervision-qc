# -*- coding: utf-8 -*-
"""LLM-as-Judge 质量评估（SRS FR-JDG-01~05）。

- 五维度打分（0-5）+ 加权总分 + 等级；
- Qwen / DeepSeek 双模型交叉评审，维度分歧超阈值标记待复核（FR-JDG-04）；
- 引用校验结果参与条款引用准确性维度校准（FR-JDG-03）；
- 总分低于阈值自动转人工复核（FR-JDG-05）。
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

from sqlalchemy.orm import Session

from app.config import settings
from app.constants import (
    DEFAULT_JUDGE_WEIGHTS,
    JUDGE_DIMENSION_LABELS,
    JUDGE_GRADE_LABELS,
    JudgeDimension,
    grade_of,
)
from app.core.logging_conf import get_logger
from app.db.models import EvalReport, JudgeReview, JudgeScore
from app.judge.citation import CitationCheckResult, check_citations_async, citation_penalty
from app.llm.gateway import ChatMessage, get_llm
from app.llm.prompts import JUDGE_SYSTEM, JUDGE_USER

logger = get_logger(__name__)

VALID_DIMENSIONS = {d.value for d in JudgeDimension}


@dataclass
class DimensionScore:
    dimension: str
    score: float
    weight: float
    comment: str = ""

    @property
    def label(self) -> str:
        try:
            return JUDGE_DIMENSION_LABELS[JudgeDimension(self.dimension)]
        except (ValueError, KeyError):
            return self.dimension

    def to_dict(self) -> dict[str, Any]:
        return {
            "dimension": self.dimension,
            "dimension_label": self.label,
            "score": round(self.score, 2),
            "weight": self.weight,
            "comment": self.comment,
        }


@dataclass
class ModelJudgement:
    model: str
    scores: list[DimensionScore] = field(default_factory=list)
    total_score: float = 0.0
    grade: str = ""
    raw: dict = field(default_factory=dict)
    error: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "model": self.model,
            "total_score": round(self.total_score, 2),
            "grade": self.grade,
            "grade_label": JUDGE_GRADE_LABELS.get(grade_of(self.total_score), ""),
            "scores": [s.to_dict() for s in self.scores],
            "error": self.error,
        }


@dataclass
class JudgeOutcome:
    total_score: float
    grade: str
    grade_label: str
    dimension_scores: list[DimensionScore]
    citation_check: dict[str, Any]
    model_results: list[ModelJudgement]
    conflicts: list[dict[str, Any]]
    has_conflict: bool
    needs_human: bool
    threshold: float
    comment: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total_score": round(self.total_score, 2),
            "grade": self.grade,
            "grade_label": self.grade_label,
            "dimension_scores": [s.to_dict() for s in self.dimension_scores],
            "citation_check": self.citation_check,
            "model_results": [
                m.to_dict() if isinstance(m, ModelJudgement) else m for m in self.model_results
            ],
            "conflicts": self.conflicts,
            "has_conflict": self.has_conflict,
            "needs_human": self.needs_human,
            "threshold": self.threshold,
            "comment": self.comment,
        }


def normalize_weights(weights: Optional[dict[str, float]] = None) -> dict[str, float]:
    source = {k.value if isinstance(k, JudgeDimension) else str(k): float(v) for k, v in (weights or DEFAULT_JUDGE_WEIGHTS).items()}
    total = sum(v for v in source.values() if v > 0)
    if total <= 0:
        return {k.value: 1.0 / len(DEFAULT_JUDGE_WEIGHTS) for k in DEFAULT_JUDGE_WEIGHTS}
    return {k: v / total for k, v in source.items()}


def _parse_scores(payload: Any, weights: dict[str, float]) -> list[DimensionScore]:
    items = payload.get("scores") if isinstance(payload, dict) else payload
    by_dimension: dict[str, DimensionScore] = {}
    for item in items or []:
        if not isinstance(item, dict):
            continue
        dimension = str(item.get("dimension") or "").strip()
        if dimension not in VALID_DIMENSIONS:
            continue
        try:
            score = float(item.get("score"))
        except (TypeError, ValueError):
            continue
        by_dimension[dimension] = DimensionScore(
            dimension=dimension,
            score=max(0.0, min(5.0, score)),
            weight=weights.get(dimension, 0.0),
            comment=str(item.get("comment") or "")[:500],
        )
    # 缺失维度按 0 分处理但显式标注，避免静默拉高总分
    for dimension in VALID_DIMENSIONS:
        if dimension not in by_dimension:
            by_dimension[dimension] = DimensionScore(
                dimension=dimension,
                score=0.0,
                weight=weights.get(dimension, 0.0),
                comment="模型未返回该维度评分，按 0 分计。",
            )
    order = [d.value for d in JudgeDimension]
    return [by_dimension[d] for d in order if d in by_dimension]


def weighted_total(scores: Sequence[DimensionScore]) -> float:
    total = sum(s.score * s.weight for s in scores)
    weight_sum = sum(s.weight for s in scores) or 1.0
    return round(total / weight_sum, 2)


async def _judge_once(model: Optional[str], prompt: str, scene: str) -> ModelJudgement:
    weights = normalize_weights()
    label = model or settings.llm_primary_model
    try:
        data, response = await get_llm().chat_json(
            [ChatMessage("system", JUDGE_SYSTEM), ChatMessage("user", prompt)],
            scene=scene,
            model=model,
            temperature=0.0,
            max_tokens=2048,
        )
        scores = _parse_scores(data, weights)
        total = weighted_total(scores)
        return ModelJudgement(
            model=response.model or label,
            scores=scores,
            total_score=total,
            grade=grade_of(total).value,
            raw=data if isinstance(data, dict) else {"scores": data},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Judge 评分失败", extra={"model": label, "error": str(exc)[:200]})
        return ModelJudgement(model=label, error=str(exc)[:300])


def _detect_conflicts(results: Sequence[ModelJudgement], delta: float) -> list[dict[str, Any]]:
    """双模型维度分歧检测（FR-JDG-04）。"""
    valid = [r for r in results if not r.error and r.scores]
    if len(valid) < 2:
        return []
    conflicts: list[dict[str, Any]] = []
    base = {s.dimension: s.score for s in valid[0].scores}
    for other in valid[1:]:
        for score in other.scores:
            reference = base.get(score.dimension)
            if reference is None:
                continue
            if abs(reference - score.score) > delta:
                conflicts.append(
                    {
                        "dimension": score.dimension,
                        "dimension_label": score.label,
                        "models": [valid[0].model, other.model],
                        "scores": [reference, score.score],
                        "delta": round(abs(reference - score.score), 2),
                    }
                )
    return conflicts


def _calibrate_with_citations(
    scores: list[DimensionScore], citation: CitationCheckResult
) -> list[DimensionScore]:
    """用引用校验结果校准「条款引用准确性」维度。

    硬约束的意义：Judge 会被流畅的文字骗过，引用存在性/一致性是可机器验证的事实，
    因此无论 Judge 给多少分，都必须按校验结果扣分。扣分按比例（见 citation_penalty），
    避免单条错误引用把维度直接打到 0 分而掩盖真实得分。
    """
    penalty = citation_penalty(citation)
    if penalty.points <= 0:
        return scores
    target = JudgeDimension.CLAUSE_CITATION_ACCURACY.value
    for item in scores:
        if item.dimension != target:
            continue
        new_score, reason = penalty.apply(item.score)
        item.score = new_score
        if reason:
            item.comment = (f"{reason}。{item.comment}")[:500]
    return scores


async def evaluate_report(
    session: Optional[Session],
    *,
    report_markdown: str,
    citations: Sequence[dict[str, Any]],
    conclusions: Optional[Sequence[str]] = None,
    weights: Optional[dict[str, float]] = None,
    threshold: Optional[float] = None,
    cross_model: bool = True,
) -> JudgeOutcome:
    """对报告执行 LLM-as-Judge 评审。"""
    threshold = settings.judge_threshold if threshold is None else threshold
    citation_result = await check_citations_async(
        session,
        citations,
        conclusions=conclusions,
        semantic_check=settings.judge_citation_check_enabled,
    )

    prompt = JUDGE_USER.format(
        report_markdown=(report_markdown or "")[:12000],
        citation_check=_citation_brief(citation_result),
    )

    tasks = [_judge_once(None, prompt, "judge_score")]
    if cross_model and settings.llm_fallback_api_key and settings.llm_fallback_base_url != settings.llm_primary_base_url:
        tasks.append(_judge_once(settings.llm_fallback_model, prompt, "judge_score"))
    model_results = list(await asyncio.gather(*tasks))

    valid = [r for r in model_results if not r.error and r.scores]
    if not valid:
        logger.error("全部 Judge 模型评分失败")
        return JudgeOutcome(
            total_score=0.0,
            grade=grade_of(0.0).value,
            grade_label=JUDGE_GRADE_LABELS[grade_of(0.0)],
            dimension_scores=[],
            citation_check=citation_result.to_dict(),
            model_results=list(model_results),
            conflicts=[],
            has_conflict=False,
            needs_human=True,
            threshold=threshold,
            comment="全部评审模型调用失败，需人工评审。",
        )

    # 主模型结果 + 引用校准
    primary = valid[0]
    primary.scores = _calibrate_with_citations(primary.scores, citation_result)
    primary.total_score = weighted_total(primary.scores)
    primary.grade = grade_of(primary.total_score).value

    conflicts = _detect_conflicts(model_results, settings.judge_conflict_delta)
    needs_human = (
        primary.total_score < threshold
        or bool(conflicts)
        or citation_result.hallucinations > 0
        or any(r.error for r in model_results)
    )

    comment_parts = []
    if primary.total_score < threshold:
        comment_parts.append(f"总分 {primary.total_score} 低于阈值 {threshold}，转人工复核。")
    if conflicts:
        comment_parts.append(f"双模型在 {len(conflicts)} 个维度上分歧超过 {settings.judge_conflict_delta} 分。")
    if citation_result.hallucinations:
        comment_parts.append(f"检出 {citation_result.hallucinations} 条幻觉引用。")

    return JudgeOutcome(
        total_score=primary.total_score,
        grade=primary.grade,
        grade_label=JUDGE_GRADE_LABELS[grade_of(primary.total_score)],
        dimension_scores=primary.scores,
        citation_check=citation_result.to_dict(),
        model_results=list(model_results),
        conflicts=conflicts,
        has_conflict=bool(conflicts),
        needs_human=needs_human,
        threshold=threshold,
        comment=" ".join(comment_parts),
    )


def _citation_brief(result: CitationCheckResult) -> str:
    if not result.total:
        return "报告中没有可校验的条款引用。"
    lines = [
        f"共 {result.total} 条引用，有效 {result.valid} 条，幻觉引用 {result.hallucinations} 条，"
        f"引用已废止规范 {result.abolished} 条，语义不一致 {result.inconsistent} 条。"
    ]
    for item in result.items[:10]:
        status = "存在" if item.exists else "不存在（幻觉）"
        flag = "，语义不一致" if item.semantic_consistent is False else ""
        lines.append(f"- {item.spec_code or ''} {item.clause_no or ''}：{status}{flag}")
    return "\n".join(lines)


def persist_outcome(
    session: Session, report: EvalReport, outcome: JudgeOutcome, *, raw: Optional[dict] = None
) -> JudgeReview:
    """把评审结果写入 judge_review / judge_score（SRS 5.2）。"""
    model_dicts = [m.to_dict() for m in outcome.model_results]
    review = JudgeReview(
        report_id=report.id,
        judge_model=(model_dicts[0].get("model") if model_dicts else None),
        total_score=outcome.total_score,
        grade=outcome.grade,
        weights=normalize_weights(),
        citation_check=outcome.citation_check,
        has_conflict=outcome.has_conflict,
        needs_human=outcome.needs_human,
        threshold=outcome.threshold,
        comment=outcome.comment,
        raw_output=raw or {"model_results": model_dicts, "conflicts": outcome.conflicts},
    )
    session.add(review)
    session.flush()
    for item in outcome.dimension_scores:
        session.add(
            JudgeScore(
                report_id=report.id,
                review_id=review.id,
                judge_model=review.judge_model,
                dimension=item.dimension,
                score=item.score,
                weight=item.weight,
                comment=item.comment,
                is_conflict=any(c["dimension"] == item.dimension for c in outcome.conflicts),
            )
        )
    session.flush()
    return review


__all__ = [
    "DimensionScore",
    "ModelJudgement",
    "JudgeOutcome",
    "evaluate_report",
    "persist_outcome",
    "normalize_weights",
    "weighted_total",
]
