# -*- coding: utf-8 -*-
"""API 请求/响应模型（SRS 第 6 章契约）。

前后端分离的契约就是这些模型，OpenAPI（/openapi.json）由 FastAPI 自动生成，
前端 ``src/api`` 按此实现类型定义。
"""
from __future__ import annotations

import datetime as dt
from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator


# --------------------------------------------------------------------------- #
# 通用
# --------------------------------------------------------------------------- #
class PageQuery(BaseModel):
    page: int = Field(1, ge=1, description="页码，从 1 起")
    page_size: int = Field(20, ge=1, le=100, description="每页条数，上限 100")


# --------------------------------------------------------------------------- #
# 认证（FR-SYS-02）
# --------------------------------------------------------------------------- #
class LoginRequest(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int
    user: "UserOut"


class RefreshRequest(BaseModel):
    refresh_token: str


class PasswordChangeRequest(BaseModel):
    """个人中心自助修改密码。

    ⚠️ ``old_password`` 必填且**必须服务端校验**：
    否则 access token 泄漏即等于账号被永久接管 ——
    攻击者可静默改密，把真实用户锁在系统外。
    """

    old_password: str = Field(..., min_length=1, max_length=128, description="当前密码")
    new_password: str = Field(..., min_length=8, max_length=128, description="新密码，至少 8 位")


class UserOut(BaseModel):
    id: int
    username: str
    # ---- 人员身份（账号与身份分离：username 是登录凭据，下面是「这个人是谁」）----
    full_name: Optional[str] = None
    employee_no: Optional[str] = None
    org_name: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None
    cert_no: Optional[str] = None
    signature: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    role: str
    specialties: list[str] = Field(default_factory=list)
    project_ids: list[int] = Field(default_factory=list)
    is_active: bool = True
    #: 管理员重置密码后置 True，前端据此提示用户尽快自行修改。
    must_change_password: bool = False

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: Optional[str] = Field(default=None, max_length=64)
    employee_no: Optional[str] = Field(default=None, max_length=64)
    org_name: Optional[str] = Field(default=None, max_length=128)
    department: Optional[str] = Field(default=None, max_length=128)
    position: Optional[str] = Field(default=None, max_length=64)
    cert_no: Optional[str] = Field(default=None, max_length=64)
    signature: Optional[str] = Field(default=None, max_length=64)
    email: Optional[str] = None
    role: str = Field("engineer", pattern="^(admin|kb_manager|engineer|expert|viewer)$")
    specialties: list[str] = Field(default_factory=list)


class UserUpdate(BaseModel):
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: Optional[str] = Field(None, pattern="^(admin|kb_manager|engineer|expert|viewer)$")
    specialties: Optional[list[str]] = None
    is_active: Optional[bool] = None
    password: Optional[str] = Field(None, min_length=8, max_length=128)


# --------------------------------------------------------------------------- #
# 知识库（FR-KB）
# --------------------------------------------------------------------------- #
class KnowledgeBaseCreate(BaseModel):
    code: str = Field(..., min_length=2, max_length=64)
    name: str = Field(..., min_length=1, max_length=128)
    specialty: Optional[str] = None
    description: Optional[str] = None


class KnowledgeBaseOut(BaseModel):
    id: int
    code: str
    name: str
    specialty: Optional[str] = None
    description: Optional[str] = None
    is_active: bool = True

    model_config = {"from_attributes": True}


class SpecDocOut(BaseModel):
    id: int
    kb_id: Optional[int] = None
    spec_code: str
    spec_name: str
    issuer: Optional[str] = None
    publish_date: Optional[dt.date] = None
    effective_date: Optional[dt.date] = None
    abolish_date: Optional[dt.date] = None
    specialty: Optional[str] = None
    region_level: Optional[str] = None
    status: str
    kg_built: bool = False
    created_at: Optional[dt.datetime] = None

    model_config = {"from_attributes": True}


class SpecDocUpdate(BaseModel):
    spec_name: Optional[str] = None
    issuer: Optional[str] = None
    publish_date: Optional[dt.date] = None
    effective_date: Optional[dt.date] = None
    abolish_date: Optional[dt.date] = None
    specialty: Optional[str] = None
    region_level: Optional[str] = Field(None, pattern="^(national|industry|local|enterprise)$")
    scope: Optional[str] = None
    kb_id: Optional[int] = None


class DocVersionOut(BaseModel):
    id: int
    doc_id: int
    version_label: str
    file_name: str
    file_size: Optional[int] = None
    file_type: Optional[str] = None
    page_count: Optional[int] = None
    parse_status: str
    parse_error: Optional[str] = None
    is_current: bool = True
    chunk_count: int = 0

    model_config = {"from_attributes": True}


class DocDetailOut(SpecDocOut):
    versions: list[DocVersionOut] = Field(default_factory=list)


class ChunkOut(BaseModel):
    id: int
    doc_id: int
    clause_no: Optional[str] = None
    chapter_path: Optional[str] = None
    chunk_index: int
    page_no: Optional[int] = None
    content: str
    token_count: Optional[int] = None
    status: str

    model_config = {"from_attributes": True}


class ChunkUpdate(BaseModel):
    content: Optional[str] = None
    clause_no: Optional[str] = None
    chapter_path: Optional[str] = None
    page_no: Optional[int] = None
    status: Optional[str] = Field(None, pattern="^(active|obsolete)$")


class ParseRequest(BaseModel):
    force: bool = Field(False, description="是否强制重新解析（丢弃已有分块）")
    build_kg: bool = Field(False, description="解析完成后是否立即构建知识图谱")


class PublishRequest(BaseModel):
    action: str = Field(..., pattern="^(publish|unpublish|abolish)$")
    reason: Optional[str] = None


class KgExtractRequest(BaseModel):
    doc_id: int
    limit: Optional[int] = Field(None, ge=1, le=2000, description="最多抽取的条款数，缺省为全部")
    concurrency: int = Field(4, ge=1, le=16)


# --------------------------------------------------------------------------- #
# 检索（FR-RET）
# --------------------------------------------------------------------------- #
class RetrievalRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    kb_ids: Optional[list[int]] = None
    specialty: Optional[str] = None
    top_k: int = Field(8, ge=1, le=50)
    enable_multi_query: bool = True
    enable_kg_expand: bool = True


class RetrievedClauseOut(BaseModel):
    chunk_id: int
    clause_no: Optional[str] = None
    spec_code: Optional[str] = None
    spec_name: Optional[str] = None
    chapter_path: Optional[str] = None
    page_no: Optional[int] = None
    content: Optional[str] = None
    rrf_score: float = 0.0
    relevance_score: float = 0.0
    source: list[str] = Field(default_factory=list)
    kg_related: list[dict[str, Any]] = Field(default_factory=list)
    citation: dict[str, Any] = Field(default_factory=dict)


class RetrievalResponse(BaseModel):
    query: str
    understanding: dict[str, Any] = Field(default_factory=dict)
    sub_queries: list[str] = Field(default_factory=list)
    results: list[RetrievedClauseOut] = Field(default_factory=list)
    no_evidence: bool = False
    latency_ms: int = 0
    degraded: bool = False
    debug: dict[str, Any] = Field(default_factory=dict)


class ChatRequest(BaseModel):
    query: str = Field(..., min_length=1, max_length=1000)
    kb_ids: Optional[list[int]] = None
    specialty: Optional[str] = None
    top_k: int = Field(8, ge=1, le=20)
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    answer: str
    no_evidence: bool
    citations: list[dict[str, Any]] = Field(default_factory=list)
    sub_queries: list[str] = Field(default_factory=list)
    latency_ms: int = 0


# --------------------------------------------------------------------------- #
# 知识图谱（FR-KG）
# --------------------------------------------------------------------------- #
class ReferenceChainResponse(BaseModel):
    clause_no: str
    direction: str = "both"
    depth: Optional[int] = None
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)
    available: bool = True


# --------------------------------------------------------------------------- #
# 评估任务（FR-AGT）
# --------------------------------------------------------------------------- #
class EvalObjectIn(BaseModel):
    part: Optional[str] = Field(None, max_length=255, description="工程部位/评估对象")
    project_name: Optional[str] = None
    description: Optional[str] = Field(None, max_length=8000, description="问题描述")
    records: list[dict[str, Any]] = Field(
        default_factory=list, description="待评材料，如 [{name, content}]"
    )

    @field_validator("records")
    @classmethod
    def _limit_records(cls, value: list[dict[str, Any]]) -> list[dict[str, Any]]:
        if len(value) > 50:
            raise ValueError("待评材料最多 50 条")
        return value


class EvalTaskCreate(BaseModel):
    project_id: Optional[int] = None
    eval_type: str = Field("inspection_lot", max_length=32)
    specialty: Optional[str] = None
    title: Optional[str] = Field(None, max_length=255)
    object: EvalObjectIn = Field(default_factory=EvalObjectIn)
    kb_ids: Optional[list[int]] = None
    options: dict[str, Any] = Field(
        default_factory=dict,
        description="可选：max_iterations / no_progress_limit / judge_threshold / kb_ids 等",
    )


class EvalSubtaskOut(BaseModel):
    id: int
    seq: int
    name: str
    criterion: Optional[str] = None
    required_evidence: Optional[str] = None
    specialty: Optional[str] = None
    query: Optional[str] = None
    status: str

    model_config = {"from_attributes": True}


class MatchResultOut(BaseModel):
    id: int
    clause_no: Optional[str] = None
    spec_code: Optional[str] = None
    spec_name: Optional[str] = None
    verdict: str
    confidence: Optional[float] = None
    relevance_score: Optional[float] = None
    evidence: Optional[str] = None
    reasoning: Optional[str] = None
    risk_level: Optional[str] = None
    remediation: Optional[str] = None
    citation_json: Optional[dict[str, Any]] = None

    model_config = {"from_attributes": True}


class AgentStepOut(BaseModel):
    id: int
    step: str
    seq: int
    status: str
    duration_ms: Optional[int] = None
    token_used: int = 0
    iteration: int = 0
    retry_count: int = 0
    output_digest: Optional[dict[str, Any]] = None
    error: Optional[str] = None

    model_config = {"from_attributes": True}


class EvalTaskOut(BaseModel):
    id: str
    project_id: Optional[int] = None
    eval_type: str
    specialty: Optional[str] = None
    title: Optional[str] = None
    current_state: str
    current_step: Optional[str] = None
    iteration_count: int = 0
    no_progress_rounds: int = 0
    progress: float = 0.0
    total_tokens: int = 0
    guard_reason: Optional[str] = None
    started_at: Optional[dt.datetime] = None
    finished_at: Optional[dt.datetime] = None
    created_at: Optional[dt.datetime] = None


class EvalTaskDetailOut(EvalTaskOut):
    input_payload: dict[str, Any] = Field(default_factory=dict)
    options: dict[str, Any] = Field(default_factory=dict)
    state_snapshot: Optional[dict[str, Any]] = None
    error_state: Optional[dict[str, Any]] = None
    subtasks: list[EvalSubtaskOut] = Field(default_factory=list)
    matches: list[MatchResultOut] = Field(default_factory=list)
    steps: list[AgentStepOut] = Field(default_factory=list)
    report_id: Optional[str] = None
    judge: Optional[dict[str, Any]] = None


class EvalTaskCreateResponse(BaseModel):
    task_id: str
    current_state: str
    thread_id: Optional[str] = None
    message: str = "任务已创建"


class ResumeRequest(BaseModel):
    action: str = Field("continue", pattern="^(continue|cancel)$")
    corrected_matches: list[dict[str, Any]] = Field(default_factory=list)
    comment: Optional[str] = None


class ReportOut(BaseModel):
    id: str
    task_id: str
    conclusion: Optional[str] = None
    overall_verdict: Optional[str] = None
    risk_level: Optional[str] = None
    summary: Optional[str] = None
    markdown: Optional[str] = None
    content: dict[str, Any] = Field(default_factory=dict)
    basis_count: int = 0
    non_compliance_count: int = 0
    generator_model: Optional[str] = None
    is_final: bool = False
    created_at: Optional[dt.datetime] = None
    judge: Optional[dict[str, Any]] = None


# --------------------------------------------------------------------------- #
# Judge（FR-JDG）
# --------------------------------------------------------------------------- #
class JudgeRequest(BaseModel):
    threshold: Optional[float] = Field(None, ge=0, le=5)
    weights: Optional[dict[str, float]] = None
    cross_model: bool = True


class JudgeScoreOut(BaseModel):
    dimension: str
    judge_model: Optional[str] = None
    score: Optional[float] = None
    weight: Optional[float] = None
    comment: Optional[str] = None
    is_conflict: bool = False

    model_config = {"from_attributes": True}


class JudgeReviewOut(BaseModel):
    id: int
    report_id: str
    judge_model: Optional[str] = None
    total_score: Optional[float] = None
    grade: Optional[str] = None
    has_conflict: bool = False
    needs_human: bool = False
    threshold: Optional[float] = None
    comment: Optional[str] = None
    citation_check: Optional[dict[str, Any]] = None
    created_at: Optional[dt.datetime] = None
    scores: list[JudgeScoreOut] = Field(default_factory=list)


class JudgeDashboardOut(BaseModel):
    review_count: int = 0
    avg_total_score: float = 0.0
    needs_human_count: int = 0
    conflict_count: int = 0
    grade_distribution: dict[str, int] = Field(default_factory=dict)
    dimension_averages: dict[str, float] = Field(default_factory=dict)
    hallucination_total: int = 0


# --------------------------------------------------------------------------- #
# 系统（FR-SYS）
# --------------------------------------------------------------------------- #
class AuditLogOut(BaseModel):
    id: int
    trace_id: Optional[str] = None
    user_id: Optional[int] = None
    username: Optional[str] = None
    action: str
    object_type: Optional[str] = None
    object_id: Optional[str] = None
    result: str
    ip: Optional[str] = None
    detail: Optional[dict[str, Any]] = None
    created_at: Optional[dt.datetime] = None

    model_config = {"from_attributes": True}


class HealthOut(BaseModel):
    status: str
    app: str
    version: str
    environment: str
    components: dict[str, Any] = Field(default_factory=dict)


class ReviewDecisionRequest(BaseModel):
    """人工复核裁定请求（FR-AGT-11 报告签发）。

    判定「不合格」时 ``comment`` 必填（裁定依据），``rerun`` 决定如何处置：
    ``True`` 驳回重跑（任务回 MATCHING 重新生成报告）、
    ``False`` 直接落定不合格（任务终结但不签发）。
    """

    #: qualified / unqualified —— 用 pattern 做枚举校验，避免自由字符串
    verdict: str = Field(..., pattern="^(qualified|unqualified)$")
    #: 裁定依据/说明。判定不合格时必填（服务层二次校验）
    comment: str = Field("", max_length=2000)
    #: 判定不合格时：是否驳回重跑
    rerun: bool = False
    #: 逐条条款判定修订（可选）
    corrected_matches: list[dict[str, Any]] = Field(default_factory=list)
    #: 乐观锁：期望的任务版本号，防止两人同时裁定
    expected_version: Optional[int] = None


class ReviewStatusResponse(BaseModel):
    """报告复核状态与复核决定。

    ⚠️ 术语对象（本项目最易混淆处，改动前请先读）
    --------------------------------------------
    本响应含**两个不同对象的判定**，二者并存、互不覆盖：

    - ``machine_*``：**AI 对工程质量的判定**（``overall_verdict``）；
    - ``review_decision*`` / ``human_verdict*``：**人工对 AI 报告的取舍**
      （报告能否对外出具）。

    人工决定的标签刻意用**动作词**（接受报告 / 退回报告）而不是
    「合格 / 不合格」：后者会让人误以为是工程质量结论。
    对象说明放在 ``review_decision_object_note``，界面须与值同时展示。
    """

    task_id: str
    report_id: Optional[str] = None
    current_state: str
    #: 机器结论（AI 生成，不被人工覆盖）。对象：**工程质量**
    machine_verdict: Optional[str] = None
    machine_verdict_label: Optional[str] = None
    #: 机器结论的对象说明
    machine_verdict_object_note: str = "AI 对工程质量的判定"
    risk_level: Optional[str] = None
    #: 人工复核决定（动作词）。对象：**AI 报告**
    review_decision: Optional[str] = None
    review_decision_label: Optional[str] = None
    review_decision_object_note: str = "人工对 AI 报告的取舍（不是判定工程质量）"
    #: 历史字段，保留兼容（值 qualified / unqualified）
    human_verdict: Optional[str] = None
    human_verdict_label: Optional[str] = None
    review_comment: Optional[str] = None
    reviewed_by: Optional[int] = None
    reviewed_by_name: Optional[str] = None
    reviewed_at: Optional[dt.datetime] = None
    is_final: bool = False
    review_status: str = "pending"
    review_status_label: str = "待复核"
    #: 复核状态的后果说明（如「不得作为正式依据」）
    review_status_note: Optional[str] = None
    version: int = 1


class FeedbackCreate(BaseModel):
    report_id: Optional[str] = None
    task_id: Optional[str] = None
    #: 反馈动作。限定取值，避免写入无意义字符串（整体裁定走 /review 接口）
    action: str = Field(..., pattern="^(correct_match|confirm|reject|amend)$")
    dimension: Optional[str] = None
    original_value: Optional[dict[str, Any]] = None
    corrected_value: Optional[dict[str, Any]] = None
    comment: Optional[str] = None


TokenResponse.model_rebuild()
