# -*- coding: utf-8 -*-
"""引用校验与 Judge 评分测试（SRS FR-JDG）。"""
from __future__ import annotations

import pytest

from app.constants import DEFAULT_JUDGE_WEIGHTS, JudgeDimension, grade_of
from app.judge.citation import CitationCheckResult, CitationCheckItem, check_citations
from app.judge.scorer import (
    DimensionScore,
    _calibrate_with_citations,
    _detect_conflicts,
    _parse_scores,
    ModelJudgement,
    normalize_weights,
    weighted_total,
)


def test_normalize_weights_sums_to_one():
    weights = normalize_weights()
    assert sum(weights.values()) == pytest.approx(1.0)
    assert weights[JudgeDimension.CLAUSE_CITATION_ACCURACY.value] > weights[
        JudgeDimension.FORMAT_COMPLIANCE.value
    ], "条款引用准确性权重应高于格式规范性"


def test_normalize_weights_handles_custom_and_degenerate_input():
    custom = normalize_weights({JudgeDimension.CLAUSE_CITATION_ACCURACY.value: 3.0})
    assert custom[JudgeDimension.CLAUSE_CITATION_ACCURACY.value] == pytest.approx(1.0)
    degenerate = normalize_weights({JudgeDimension.CLAUSE_CITATION_ACCURACY.value: 0.0})
    assert sum(degenerate.values()) == pytest.approx(1.0), "非法权重应回退为均分"


def test_weighted_total_uses_weights():
    scores = [
        DimensionScore(JudgeDimension.CLAUSE_CITATION_ACCURACY.value, 5.0, 0.5),
        DimensionScore(JudgeDimension.CONCLUSION_REASONABLENESS.value, 3.0, 0.5),
    ]
    assert weighted_total(scores) == pytest.approx(4.0)


def test_parse_scores_clamps_and_fills_missing_dimensions():
    weights = normalize_weights()
    scores = _parse_scores(
        {
            "scores": [
                {"dimension": "clause_citation_accuracy", "score": 9.5, "comment": "越界"},
                {"dimension": "conclusion_reasonableness", "score": -3, "comment": "越界"},
                {"dimension": "不存在的维度", "score": 4.0},
            ]
        },
        weights,
    )
    by_dim = {s.dimension: s for s in scores}
    assert by_dim["clause_citation_accuracy"].score == 5.0, "超上限应钳制到 5"
    assert by_dim["conclusion_reasonableness"].score == 0.0, "低于 0 应钳制到 0"
    assert "不存在的维度" not in by_dim
    # 缺失维度按 0 分补全并给出说明，避免静默拉高总分
    assert by_dim["format_compliance"].score == 0.0
    assert "未返回" in by_dim["format_compliance"].comment


def test_parse_scores_accepts_bare_list():
    scores = _parse_scores(
        [{"dimension": "format_compliance", "score": 4.0, "comment": "ok"}], normalize_weights()
    )
    assert any(s.dimension == "format_compliance" and s.score == 4.0 for s in scores)


def test_citation_calibration_penalizes_hallucinations_proportionally():
    """FR-JDG-03：引用存在性可机器验证，必须按校验结果扣分。

    同时要求扣分**按比例**：1 条错误引用不应让维度直接归零（会掩盖真实得分）。
    """
    scores = [DimensionScore(JudgeDimension.CLAUSE_CITATION_ACCURACY.value, 5.0, 0.3, "看起来很好")]
    check = CitationCheckResult(total=6, valid=4, hallucinations=2)
    calibrated = _calibrate_with_citations(scores, check)
    assert 0.0 < calibrated[0].score < 5.0, f"应扣分但不归零，实际 {calibrated[0].score}"
    assert "引用校验" in calibrated[0].comment
    assert "准确率" in calibrated[0].comment

    # 单条幻觉引用：扣分后仍保留大部分得分
    single = _calibrate_with_citations(
        [DimensionScore(JudgeDimension.CLAUSE_CITATION_ACCURACY.value, 4.5, 0.3)],
        CitationCheckResult(total=6, valid=5, hallucinations=1),
    )
    assert single[0].score > 0.0, "1 条错误不应归零"
    assert single[0].score < 4.5

    # 扣分存在上限，避免极端情况出现负分
    heavy = _calibrate_with_citations(
        [DimensionScore(JudgeDimension.CLAUSE_CITATION_ACCURACY.value, 5.0, 0.3)],
        CitationCheckResult(total=10, valid=0, hallucinations=10),
    )
    assert heavy[0].score >= 0.0


def test_citation_calibration_penalizes_abolished_and_inconsistent():
    scores = [DimensionScore(JudgeDimension.CLAUSE_CITATION_ACCURACY.value, 4.5, 0.3)]
    check = CitationCheckResult(total=4, valid=4, abolished=1, inconsistent=1)
    calibrated = _calibrate_with_citations(scores, check)
    # 0.5（废止）+ 0.25（语义不一致）= 0.75
    assert calibrated[0].score == pytest.approx(3.75, abs=0.01)


def test_citation_calibration_noop_when_no_citations():
    scores = [DimensionScore(JudgeDimension.CLAUSE_CITATION_ACCURACY.value, 4.0, 0.3)]
    calibrated = _calibrate_with_citations(scores, CitationCheckResult(total=0))
    assert calibrated[0].score == 4.0
    assert calibrated[0].comment == ""


def test_citation_penalty_reports_reasons():
    from app.judge.citation import citation_penalty

    penalty = citation_penalty(CitationCheckResult(total=6, valid=5, hallucinations=1))
    assert penalty.points > 0
    assert any("不存在" in reason for reason in penalty.reasons)
    assert any("准确率" in reason for reason in penalty.reasons)
    assert citation_penalty(CitationCheckResult(total=0)).points == 0.0


def test_conflict_detection_between_two_models():
    weights = normalize_weights()
    model_a = ModelJudgement(
        "deepseek-chat",
        scores=[DimensionScore(JudgeDimension.CLAUSE_CITATION_ACCURACY.value, 5.0, weights[JudgeDimension.CLAUSE_CITATION_ACCURACY.value])],
    )
    model_b = ModelJudgement(
        "qwen-plus",
        scores=[DimensionScore(JudgeDimension.CLAUSE_CITATION_ACCURACY.value, 3.0, weights[JudgeDimension.CLAUSE_CITATION_ACCURACY.value])],
    )
    conflicts = _detect_conflicts([model_a, model_b], delta=1.0)
    assert len(conflicts) == 1
    assert conflicts[0]["dimension"] == "clause_citation_accuracy"
    assert conflicts[0]["delta"] == pytest.approx(2.0)


def test_conflict_detection_ignores_single_model_or_failed_model():
    weights = normalize_weights()
    ok = ModelJudgement("a", scores=[DimensionScore("format_compliance", 5.0, weights["format_compliance"])])
    failed = ModelJudgement("b", error="timeout")
    assert _detect_conflicts([ok], delta=0.5) == []
    assert _detect_conflicts([ok, failed], delta=0.5) == []


def test_grade_thresholds():
    assert grade_of(4.8).value == "excellent"
    assert grade_of(4.2).value == "good"
    assert grade_of(3.2).value == "qualified"
    assert grade_of(2.0).value == "unqualified"


def test_check_citations_detects_nonexistent_clause_without_db():
    """无数据库连接时，条款应被判定为不存在（保守策略，避免放过幻觉）。"""
    result = check_citations(
        None,
        [
            {"clause_no": "5.3.3", "spec_code": "GB 50204-2015"},
            {"clause_no": "99.9.9", "spec_code": "GB 00000-0000"},
        ],
        use_graph=False,
    )
    assert result.total == 2
    assert result.hallucinations == 2, "无任何来源可验证时应全部标记为不可验证"
    assert result.accuracy == 0.0
    assert all(item.is_hallucination for item in result.items)


def test_citation_item_to_dict_shape():
    item = CitationCheckItem(clause_no="1.2.3", spec_code="GB 1-2020", exists=True, source="db")
    payload = item.to_dict()
    assert payload["hallucination"] is False
    assert payload["clause_no"] == "1.2.3"
    assert payload["source"] == "db"


def test_default_weights_cover_all_dimensions():
    assert set(normalize_weights().keys()) == {d.value for d in JudgeDimension}
    assert sum(DEFAULT_JUDGE_WEIGHTS.values()) == pytest.approx(1.0)
