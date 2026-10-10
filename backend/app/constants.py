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

#: 各状态对应的进度百分比。
#:
#: 注意这是**由状态派生的离散值**，不是真实测量的完成度。
#: 放在这里（而非 agent 模块）是为了让「人工裁定」等非 Agent 路径也能同步进度 ——
#: 否则会出现「current_state=COMPLETED 但 progress 仍是 0.9」的数据不一致
#: （实测签发后正是如此，界面上的进度条与状态互相矛盾）。
STATE_PROGRESS: dict[EvalState, float] = {
    EvalState.PENDING: 0.0,
    EvalState.PLANNING: 0.15,
    EvalState.RETRIEVING: 0.35,
    EvalState.MATCHING: 0.55,
    EvalState.ANALYZING: 0.75,
    EvalState.REPORTING: 0.9,
    EvalState.JUDGING: 0.95,
    # NEED_HUMAN 取 0.9：Agent 已完成自评，尚待人工介入。
    # ⚠️ 界面**不应**把它当作「进行中」显示进度条 —— 会被误读为「卡在 90%」。
    # 前端对已停止状态改为显示「等待人工复核」等状态说明。
    EvalState.NEED_HUMAN: 0.9,
    EvalState.COMPLETED: 1.0,
    EvalState.DEGRADED: 1.0,
    EvalState.FAILED: 1.0,
    EvalState.CANCELLED: 1.0,
}


def progress_of(state: EvalState) -> float:
    """状态 → 进度。未知状态返回 0.0（不抛异常，避免影响主流程）。"""
    return STATE_PROGRESS.get(state, 0.0)


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


class HumanVerdict(StrEnum):
    """人工复核裁定（终审结论）。

    SRS 角色定义（第 92 行）要求质量评估专家「复核与裁定、修订评分、**终审签发**」；
    业务流程（第 142 行）为「专家复核**签发** → 报告归档 → 全链路留痕」。

    ⚠️ 这是**人工**结论，与 ``EvalReport.overall_verdict``（机器结论）并存，
    **不得互相覆盖** —— 机器结论是 AI 质量的原始证据（FR-JDG-06 反馈闭环、
    模型迭代对比都要用），人工裁定是责任判定。两者不一致本身是重要样本。
    """

    QUALIFIED = "qualified"
    UNQUALIFIED = "unqualified"


HUMAN_VERDICT_LABELS = {
    HumanVerdict.QUALIFIED: "合格",
    HumanVerdict.UNQUALIFIED: "不合格",
}


class ReviewDecision(StrEnum):
    """报告复核决定（**面向对象是「报告」，不是工程质量**）。

    ⚠️ 术语说明（这是本项目最容易混淆的地方）
    -----------------------------------------
    系统里存在**两个不同对象的判定**，早期版本共用了「合格/不合格」一个词，
    导致用户无法分辨「是 AI 报告的结果合格，还是评估项目本身合格」：

    ==========  ======================  ========  ====================
    判定对象     含义                    判定人     取值
    ==========  ======================  ========  ====================
    工程质量     AI 对实体/检验批的判定    AI        ``Verdict``（符合/不符合）
    **AI 报告**  **能否对外出具**          **人工**  ``ReviewDecision``
    ==========  ======================  ========  ====================

    本枚举是**后者**，语义来自 SRS 第 92 行：质量评估专家的职责是
    「复核**评估结论**、修订评分、**终审签发**」——
    复核对象是**报告与其中的评估结论**，不是工程实体。

    **显示用动作词而非状态词**：「接受报告」不可能被误读成「工程合格」，
    而「复核合格」一定会被误读。动作词天然绑定对象。

    ⚠️ 与 ``HumanVerdict`` 的关系：本枚举是 ``HumanVerdict`` 的**展示层映射**，
    不新增数据库字段 —— 两者一一对应（qualified→accept、unqualified→return），
    拆成两个字段在当前流程下总是同步，只会增加不一致的风险。
    """

    #: 接受 AI 报告并签发（可作为正式依据）
    ACCEPT = "accept"
    #: 退回 AI 报告，不予签发
    RETURN = "return"


REVIEW_DECISION_LABELS = {
    ReviewDecision.ACCEPT: "接受报告",
    ReviewDecision.RETURN: "退回报告",
}

#: 复核决定的对象说明（界面上必须与决定值同时出现，避免脱离语境被误读）
REVIEW_DECISION_OBJECT_NOTE = "人工对 AI 报告的取舍（不是判定工程质量）"

#: 评估结论的对象说明
VERDICT_OBJECT_NOTE = "AI 对工程质量的判定"

#: 两个判定的并列说明（详情页在两栏之间展示，解释为何可以「不符合 + 接受」）
DUAL_VERDICT_NOTE = "两者判定对象不同，结论可并存、不矛盾"


def review_decision_of(human_verdict: str | None) -> ReviewDecision | None:
    """由 ``HumanVerdict`` 派生复核决定；未裁定返回 ``None``。

    未裁定返回 ``None`` 而不是默认值 —— 界面才能正确显示「待复核」，
    而不是让人误以为已被接受或退回。
    """
    if not human_verdict:
        return None
    if human_verdict == HumanVerdict.QUALIFIED.value:
        return ReviewDecision.ACCEPT
    if human_verdict == HumanVerdict.UNQUALIFIED.value:
        return ReviewDecision.RETURN
    return None


def review_decision_label(human_verdict: str | None) -> str | None:
    """复核决定的中文标签（动作词）。"""
    decision = review_decision_of(human_verdict)
    return REVIEW_DECISION_LABELS.get(decision) if decision else None


class ReviewStatus(StrEnum):
    """报告复核状态（展示用，由 human_verdict + is_final 派生）。"""

    #: 待复核：处于可复核状态且尚无人工裁定
    PENDING = "pending"
    #: 已复核合格并签发（is_final=True，可作为正式依据）
    SIGNED = "signed"
    #: 已复核不合格（不予签发）
    REJECTED = "rejected"


REVIEW_STATUS_LABELS = {
    ReviewStatus.PENDING: "待复核",
    ReviewStatus.SIGNED: "已签发",
    # 旧值是「已复核不合格」——「不合格」会与工程质量结论混淆：
    # 这里指的是**报告被退回、未予签发**，不是工程不合格。
    ReviewStatus.REJECTED: "已退回",
}

#: 复核状态的补充说明（界面在状态旁展示其后果，避免用户只看标签猜含义）
REVIEW_STATUS_NOTES = {
    ReviewStatus.PENDING: "尚无人工复核决定，不得作为正式依据",
    ReviewStatus.SIGNED: "已经人工复核签发，可作为正式依据",
    ReviewStatus.REJECTED: "报告未通过复核、未予签发，不得作为正式依据",
}


def review_status_of(human_verdict: str | None, is_final: bool) -> ReviewStatus:
    """由人工裁定与签发标记推导展示状态。

    未裁定（``human_verdict`` 为 ``None``）一律视为待复核 ——
    包括评分达标但未经人工确认的报告：**没有签发就不算正式报告**
    （FR-JDG-05「不直接出具正式报告」的语义）。
    """
    if not human_verdict:
        return ReviewStatus.PENDING
    if human_verdict == HumanVerdict.QUALIFIED.value:
        return ReviewStatus.SIGNED if is_final else ReviewStatus.PENDING
    return ReviewStatus.REJECTED


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
    #: 人工复核裁定与签发（FR-AGT-11 报告签发）
    REPORT_REVIEW = "report_review"


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


#: `EvalReport.overall_verdict` 采用**两套词汇之一**，取决于报告怎么生成的：
#:
#: - 有 Judge 总分时 → 取 **等级**（`JudgeGrade`：优秀/良好/合格/不合格）；
#: - 仅按条款判定汇总时 → 取 **条款判定**（`Verdict`：符合/不符合/部分符合…）。
#:
#: 因此解析标签**必须两套都试**。只认其中一套会在另一种报告上抛 `ValueError`
#: 导致接口 500 —— 这个坑出现过两次：先用 `Verdict` 解析等级值，
#: 改成 `JudgeGrade` 后又解析不了条款值。放在文件末尾是因为它依赖
#: 上面所有枚举的定义。
def overall_verdict_label(value: str | None) -> str | None:
    """把 `overall_verdict` 转成中文标签，兼容等级与条款判定两套词汇。"""
    if not value:
        return None
    for enum_cls, labels in (
        (JudgeGrade, JUDGE_GRADE_LABELS),
        (Verdict, VERDICT_LABELS),
    ):
        try:
            return labels[enum_cls(value)]
        except (ValueError, KeyError):
            continue
    # 未知取值原样返回，不抛异常 —— 展示层不该因为多了一个枚举值就 500
    return value
