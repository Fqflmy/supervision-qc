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


class UserOut(BaseModel):
    id: int
    username: str
    full_name: Optional[str] = None
    email: Optional[str] = None
    role: str
    specialties: list[str] = Field(default_factory=list)
    project_ids: list[int] = Field(default_factory=list)
    is_active: bool = True

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=8, max_length=128)
    full_name: Optional[str] = None
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


class FeedbackCreate(BaseModel):
    report_id: Optional[str] = None
    task_id: Optional[str] = None
    action: str = Field(..., max_length=32)
    dimension: Optional[str] = None
    original_value: Optional[dict[str, Any]] = None
    corrected_value: Optional[dict[str, Any]] = None
    comment: Optional[str] = None


TokenResponse.model_rebuild()
