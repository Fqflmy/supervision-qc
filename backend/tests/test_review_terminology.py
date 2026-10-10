# -*- coding: utf-8 -*-
"""复核结论术语防歧义测试。

背景
----
用户反馈：「复核结论合格是对 AI 报告的结果合格还是评估项目合格，容易让人混淆」。

根因：系统里有两个**不同对象**的判定，早期都用「合格/不合格」显示：
- 工程质量 → AI 判定（``Verdict``：符合 / 不符合）
- AI 报告  → 人工决定（``HumanVerdict``）

本测试锁定整改后的术语口径，防止回归：

1. 复核决定的显示标签必须是**动作词**（接受报告 / 退回报告），
   不得是「合格 / 不合格」；
2. 复核状态标签不得用「已复核不合格」（会与工程质量混淆），应为「已退回」；
3. 每个结论都必须带**对象说明**（AI 判工程质量 / 人工判报告）；
4. `None`（未复核）必须显示为「待复核」，不得默认成「接受」或「退回」。
"""
from __future__ import annotations

import pytest

from app.constants import (
    DUAL_VERDICT_NOTE,
    HUMAN_VERDICT_LABELS,
    REVIEW_DECISION_LABELS,
    REVIEW_DECISION_OBJECT_NOTE,
    REVIEW_STATUS_LABELS,
    REVIEW_STATUS_NOTES,
    VERDICT_OBJECT_NOTE,
    HumanVerdict,
    ReviewDecision,
    ReviewStatus,
    review_decision_label,
    review_decision_of,
    review_status_of,
)

#: 会与「工程质量」混淆的词 —— 复核决定的标签里绝不允许出现
AMBIGUOUS_WORDS = ("合格", "不合格")


class TestReviewDecisionLabels:
    """复核决定必须用动作词，且不得含「合格」字样。"""

    def test_accept_label_is_action_word(self):
        label = REVIEW_DECISION_LABELS[ReviewDecision.ACCEPT]
        assert label == "接受报告", label
        assert "报告" in label, "标签必须点明对象是报告"

    def test_return_label_is_action_word(self):
        label = REVIEW_DECISION_LABELS[ReviewDecision.RETURN]
        assert label == "退回报告", label
        assert "报告" in label

    @pytest.mark.parametrize("decision", list(ReviewDecision))
    def test_decision_labels_contain_no_ambiguous_words(self, decision):
        label = REVIEW_DECISION_LABELS[decision]
        for word in AMBIGUOUS_WORDS:
            assert word not in label, (
                f"复核决定标签「{label}」含「{word}」—— "
                "会被误读成工程质量结论，必须用动作词"
            )

    def test_decision_object_note_states_object(self):
        """对象说明必须明确「不是判定工程质量」，否则用户仍会误读。"""
        assert "报告" in REVIEW_DECISION_OBJECT_NOTE
        assert "不是判定工程质量" in REVIEW_DECISION_OBJECT_NOTE

    def test_verdict_object_note_states_object(self):
        assert "工程质量" in VERDICT_OBJECT_NOTE

    def test_dual_note_explains_coexistence(self):
        """必须有一句话解释「为何可以 不符合 + 接受报告」。"""
        assert "并存" in DUAL_VERDICT_NOTE or "不矛盾" in DUAL_VERDICT_NOTE


class TestReviewDecisionMapping:
    """历史字段 human_verdict → 动作词标签的映射。"""

    def test_qualified_maps_to_accept(self):
        assert review_decision_of("qualified") is ReviewDecision.ACCEPT
        assert review_decision_label("qualified") == "接受报告"

    def test_unqualified_maps_to_return(self):
        assert review_decision_of("unqualified") is ReviewDecision.RETURN
        assert review_decision_label("unqualified") == "退回报告"

    def test_none_means_not_reviewed(self):
        """未复核必须返回 None，不能默认成任一决定。"""
        assert review_decision_of(None) is None
        assert review_decision_label(None) is None

    def test_unknown_value_returns_none_not_wrong_label(self):
        """脏数据不得被误标成「接受报告」——宁可显示未复核。"""
        assert review_decision_of("garbage") is None
        assert review_decision_label("garbage") is None

    def test_human_verdict_labels_still_exist_for_compat(self):
        """历史字段标签保留（数据库与旧接口仍用 qualified/unqualified）。"""
        assert HUMAN_VERDICT_LABELS[HumanVerdict.QUALIFIED] == "合格"
        assert HUMAN_VERDICT_LABELS[HumanVerdict.UNQUALIFIED] == "不合格"


class TestReviewStatusLabels:
    """复核状态标签：不得与工程质量措辞混淆。"""

    def test_rejected_label_is_not_ambiguous(self):
        label = REVIEW_STATUS_LABELS[ReviewStatus.REJECTED]
        assert label == "已退回", f"应为「已退回」，实际 {label!r}"
        for word in AMBIGUOUS_WORDS:
            assert word not in label, f"「{label}」含「{word}」，会与工程质量混淆"

    def test_signed_label(self):
        assert REVIEW_STATUS_LABELS[ReviewStatus.SIGNED] == "已签发"

    def test_pending_label(self):
        assert REVIEW_STATUS_LABELS[ReviewStatus.PENDING] == "待复核"

    @pytest.mark.parametrize("status", list(ReviewStatus))
    def test_every_status_has_consequence_note(self, status):
        """每个状态都要说明后果（能否作为正式依据），否则用户只看标签会猜错。"""
        note = REVIEW_STATUS_NOTES[status]
        assert note, f"{status} 缺少后果说明"
        if status is ReviewStatus.SIGNED:
            assert "可作为正式依据" in note
        else:
            assert "不得作为正式依据" in note, note


class TestReviewStatusDerivation:
    """状态派生：未复核一律待复核（没有签发就不算正式报告）。"""

    def test_no_verdict_is_pending_even_if_flag_set(self):
        # 极端情况：is_final 被置 True 但没有人工决定 —— 仍必须是待复核
        assert review_status_of(None, True) is ReviewStatus.PENDING
        assert review_status_of(None, False) is ReviewStatus.PENDING

    def test_qualified_with_final_is_signed(self):
        assert review_status_of("qualified", True) is ReviewStatus.SIGNED

    def test_qualified_without_final_is_pending(self):
        assert review_status_of("qualified", False) is ReviewStatus.PENDING

    def test_unqualified_is_rejected(self):
        assert review_status_of("unqualified", False) is ReviewStatus.REJECTED


class TestPayloadFields:
    """接口返回的复核载荷必须含对象说明字段（前端据此展示）。"""

    def test_payload_includes_object_notes(self):
        from app.services.review import report_review_payload

        payload = report_review_payload(None)
        assert payload["review_decision_object_note"] == REVIEW_DECISION_OBJECT_NOTE
        assert payload["review_status_note"]
        # 未复核时不得给出决定标签
        assert payload["review_decision"] is None
        assert payload["review_decision_label"] is None

    def test_payload_ok_path_has_decision_label(self):
        """有报告且已复核时，payload 应给出动作词与签发状态。"""
        from app.db.models import EvalReport
        from app.services.review import report_review_payload

        report = EvalReport(
            task_id=None,
            overall_verdict="non_compliant",
            human_verdict="qualified",
            is_final=True,
            review_comment="已核对",
        )
        payload = report_review_payload(report)
        assert payload["review_decision"] == "accept"
        assert payload["review_decision_label"] == "接受报告"
        assert payload["review_status"] == "signed"
        assert "可作为正式依据" in payload["review_status_note"]
        # 关键场景：AI 判「不符合」+ 人工「接受报告」并存，互不覆盖
        assert report.overall_verdict == "non_compliant"
        assert payload["human_verdict"] == "qualified"
