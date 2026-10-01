# -*- coding: utf-8 -*-
"""领域常量与枚举。与 SRS 第 3、4、5 章一一对应。"""
from __future__ import annotations

from enum import Enum


class StrEnum(str, Enum):
    def __str__(self) -> str:  # pragma: no cover
        return self.value


class DocStatus(StrEnum):
    """规范文档状态（FR-KB-08/09）。"""

    DRAFT = "draft"
    PARSING = "parsing"
    PENDING_REVIEW = "pending_review"
    PUBLISHED = "published"
    ABOLISHED = "abolished"
    FAILED = "failed"


class ChunkStatus(StrEnum):
    """文本块状态（SRS 5.2 doc_chunk.status）。"""

    ACTIVE = "active"
    OBSOLETE = "obsolete"


class RegionLevel(StrEnum):
    """规范层级。"""

    NATIONAL = "national"
    INDUSTRY = "industry"
    LOCAL = "local"
    ENTERPRISE = "enterprise"


class EvalState(StrEnum):
    """Agent 任务状态机（SRS 4.1）。"""

    PENDING = "PENDING"
    PLANNING = "PLANNING"
    RETRIEVING = "RETRIEVING"
    MATCHING = "MATCHING"
    ANALYZING = "ANALYZING"
    REPORTING = "REPORTING"
    JUDGING = "JUDGING"
    NEED_HUMAN = "NEED_HUMAN"
    DEGRADED = "DEGRADED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


#: SRS 4.1 状态迁移表：允许的下一状态
STATE_TRANSITIONS: dict[EvalState, set[EvalState]] = {
    EvalState.PENDING: {EvalState.PLANNING, EvalState.CANCELLED},
    EvalState.PLANNING: {EvalState.RETRIEVING, EvalState.FAILED},
    EvalState.RETRIEVING: {EvalState.MATCHING, EvalState.DEGRADED},
    EvalState.MATCHING: {EvalState.ANALYZING, EvalState.NEED_HUMAN},
    EvalState.ANALYZING: {EvalState.REPORTING, EvalState.DEGRADED},
    EvalState.REPORTING: {EvalState.JUDGING, EvalState.FAILED},
    EvalState.JUDGING: {EvalState.COMPLETED, EvalState.NEED_HUMAN},
    EvalState.NEED_HUMAN: {EvalState.MATCHING, EvalState.COMPLETED, EvalState.CANCELLED},
    EvalState.DEGRADED: {EvalState.NEED_HUMAN, EvalState.COMPLETED},
    EvalState.COMPLETED: set(),
    EvalState.FAILED: set(),
    EvalState.CANCELLED: set(),
}

TERMINAL_STATES = {EvalState.COMPLETED, EvalState.FAILED, EvalState.CANCELLED}
REVIEWABLE_STATES = {EvalState.NEED_HUMAN, EvalState.DEGRADED}


class Verdict(StrEnum):
    """条款匹配结论（FR-AGT-05）。"""

    COMPLIANT = "compliant"
    NON_COMPLIANT = "non_compliant"
    PARTIAL = "partial"
    NOT_APPLICABLE = "not_applicable"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


VERDICT_LABELS = {
    Verdict.COMPLIANT: "符合",
    Verdict.NON_COMPLIANT: "不符合",
    Verdict.PARTIAL: "部分符合",
    Verdict.NOT_APPLICABLE: "不适用",
    Verdict.INSUFFICIENT_EVIDENCE: "证据不足",
}


class RiskLevel(StrEnum):
    """风险等级（FR-AGT-06）。"""

    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class JudgeDimension(StrEnum):
    """Judge 评分维度（FR-JDG-01）。"""

    CLAUSE_CITATION_ACCURACY = "clause_citation_accuracy"
    CONCLUSION_REASONABLENESS = "conclusion_reasonableness"
    EVIDENCE_SUFFICIENCY = "evidence_sufficiency"
    FORMAT_COMPLIANCE = "format_compliance"
    REMEDIATION_ACTIONABILITY = "remediation_actionability"


JUDGE_DIMENSION_LABELS = {
    JudgeDimension.CLAUSE_CITATION_ACCURACY: "条款引用准确性",
    JudgeDimension.CONCLUSION_REASONABLENESS: "结论合理性",
    JudgeDimension.EVIDENCE_SUFFICIENCY: "证据充分性",
    JudgeDimension.FORMAT_COMPLIANCE: "格式规范性",
    JudgeDimension.REMEDIATION_ACTIONABILITY: "整改建议可执行性",
}

#: FR-JDG-02 默认权重
DEFAULT_JUDGE_WEIGHTS = {
    JudgeDimension.CLAUSE_CITATION_ACCURACY: 0.30,
    JudgeDimension.CONCLUSION_REASONABLENESS: 0.25,
    JudgeDimension.EVIDENCE_SUFFICIENCY: 0.20,
    JudgeDimension.FORMAT_COMPLIANCE: 0.10,
    JudgeDimension.REMEDIATION_ACTIONABILITY: 0.15,
}


class JudgeGrade(StrEnum):
    EXCELLENT = "excellent"
    GOOD = "good"
    QUALIFIED = "qualified"
    UNQUALIFIED = "unqualified"


JUDGE_GRADE_LABELS = {
    JudgeGrade.EXCELLENT: "优秀",
    JudgeGrade.GOOD: "良好",
    JudgeGrade.QUALIFIED: "合格",
    JudgeGrade.UNQUALIFIED: "不合格",
}


def grade_of(score: float) -> JudgeGrade:
    """FR-JDG-02：总分 → 等级。"""
    if score >= 4.5:
        return JudgeGrade.EXCELLENT
    if score >= 4.0:
        return JudgeGrade.GOOD
    if score >= 3.0:
        return JudgeGrade.QUALIFIED
    return JudgeGrade.UNQUALIFIED


class KgNodeLabel(StrEnum):
    """SRS 5.4 节点标签。"""

    SPEC = "Spec"
    CHAPTER = "Chapter"
    CLAUSE = "Clause"
    TERM = "Term"
    PART = "Part"
    MATERIAL = "Material"
    INDICATOR = "Indicator"
    ORG = "Org"


class KgRelation(StrEnum):
    """SRS 5.4 关系类型。"""

    CONTAINS = "CONTAINS"
    REFERENCES = "REFERENCES"
    SUPERSEDES = "SUPERSEDES"
    REFINES = "REFINES"
    CONFLICTS_WITH = "CONFLICTS_WITH"
    APPLIES_TO = "APPLIES_TO"
    DEFINES = "DEFINES"
    ISSUED_BY = "ISSUED_BY"


class AgentStep(StrEnum):
    """LangGraph 五阶段节点名（FR-AGT-02）。"""

    PLANNING = "planning"
    RETRIEVAL = "retrieval"
    MATCHING = "clause_matching"
    ANALYSIS = "analysis"
    REPORT = "report_generation"


AGENT_STEP_ORDER = [
    AgentStep.PLANNING,
    AgentStep.RETRIEVAL,
    AgentStep.MATCHING,
    AgentStep.ANALYSIS,
    AgentStep.REPORT,
]

AGENT_STEP_LABELS = {
    AgentStep.PLANNING: "任务拆解",
    AgentStep.RETRIEVAL: "规范检索",
    AgentStep.MATCHING: "条款匹配",
    AgentStep.ANALYSIS: "结果分析",
    AgentStep.REPORT: "报告生成",
}

STEP_TO_STATE = {
    AgentStep.PLANNING: EvalState.PLANNING,
    AgentStep.RETRIEVAL: EvalState.RETRIEVING,
    AgentStep.MATCHING: EvalState.MATCHING,
    AgentStep.ANALYSIS: EvalState.ANALYZING,
    AgentStep.REPORT: EvalState.REPORTING,
}


class AuditAction(StrEnum):
    LOGIN = "login"
    LOGOUT = "logout"
    DOC_UPLOAD = "doc_upload"
    DOC_PARSE = "doc_parse"
    DOC_PUBLISH = "doc_publish"
    KG_EXTRACT = "kg_extract"
    RETRIEVAL = "retrieval"
    CHAT = "chat"
    EVAL_CREATE = "eval_create"
    EVAL_RESUME = "eval_resume"
    REPORT_EXPORT = "report_export"
    JUDGE_SCORE = "judge_score"
    CONFIG_CHANGE = "config_change"


#: 错误码表（SRS 6.4）
ERROR_CODES = {
    "OK": 0,
    "PARAM_INVALID": 40001,
    "UNAUTHENTICATED": 40101,
    "TOKEN_EXPIRED": 40102,
    "FORBIDDEN": 40301,
    "NOT_FOUND": 40401,
    "CONFLICT": 40901,
    "INTERNAL_ERROR": 50001,
    "LLM_FAILED": 60001,
    "VECTOR_SEARCH_FAILED": 60002,
    "STRUCTURED_OUTPUT_FAILED": 60003,
    "KG_FAILED": 60004,
    "AGENT_GUARD_TRIGGERED": 60005,
    "NO_EVIDENCE": 60006,
}
