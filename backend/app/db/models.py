# -*- coding: utf-8 -*-
"""ORM 模型（严格对应 SRS 5.2 表结构）。"""
from __future__ import annotations

import datetime as dt
import uuid
from typing import Any, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, TypeDecorator

from app.constants import ChunkStatus, DocStatus, EvalState


class Base(DeclarativeBase):
    pass


class JSONType(TypeDecorator):
    """PostgreSQL 用 JSONB，其他方言退化 JSON（便于单元测试）。"""

    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class GUID(TypeDecorator):
    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(PGUUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        if dialect.name == "postgresql":
            return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
        return str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


def _now() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


class TimestampMixin:
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, onupdate=_now, server_default=func.now(), nullable=False
    )


# --------------------------------------------------------------------------- #
# 组织与用户（FR-SYS-01/02）
# --------------------------------------------------------------------------- #
class Project(Base, TimestampMixin):
    __tablename__ = "project"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    owner_org: Mapped[Optional[str]] = mapped_column(String(128))
    supervision_org: Mapped[Optional[str]] = mapped_column(String(128))
    specialty: Mapped[Optional[str]] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default="active", nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text)

    tasks: Mapped[list["EvalTask"]] = relationship(back_populates="project")


class User(Base, TimestampMixin):
    __tablename__ = "sys_user"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    # ---- 人员身份绑定 ----
    # 账号（username/password/role）是**登录凭据与权限载体**；
    # 下面这些字段绑定「这个账号对应现实中的谁」。
    # 为什么必须分开：评估报告要签认到**具体的人**（监理规范要求责任到人），
    # 而账号名往往是无意义的登录 ID（如 eng_zhao）。
    full_name: Mapped[Optional[str]] = mapped_column(String(64))
    #: 工号。单位内部唯一标识，用于与人事/项目台账对齐。
    employee_no: Mapped[Optional[str]] = mapped_column(String(64), unique=True)
    #: 所属单位（建设单位 / 监理单位 / 施工单位…）
    org_name: Mapped[Optional[str]] = mapped_column(String(128))
    #: 所属部门（如「项目监理部」「技术质量部」）
    department: Mapped[Optional[str]] = mapped_column(String(128))
    #: 职务/岗位（如「总监理工程师」「专业监理工程师」）
    position: Mapped[Optional[str]] = mapped_column(String(64))
    #: 执业资格证号（如注册监理工程师证号），监理场景常需在报告中体现
    cert_no: Mapped[Optional[str]] = mapped_column(String(64))
    #: 签认署名。留空时报告与审计展示回退到 full_name。
    #: 用文本而非签名图片：签名图需额外存储与合规流程，当前阶段文本署名已满足留痕要求。
    signature: Mapped[Optional[str]] = mapped_column(String(64))
    email: Mapped[Optional[str]] = mapped_column(String(128))
    phone: Mapped[Optional[str]] = mapped_column(String(32))
    password_hash: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(32), default="engineer", nullable=False)
    project_ids: Mapped[Optional[list]] = mapped_column(JSONType, default=list)
    specialties: Mapped[Optional[list]] = mapped_column(JSONType, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: 是否要求下次登录后修改密码。
    #: 管理员重置密码时置 True —— 与「首次登录须改初始密码」的安全要求一致，
    #: 避免管理员设置的临时密码被长期使用。
    must_change_password: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    failed_login_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    locked_until: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    last_login_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))

    __table_args__ = (Index("ix_sys_user_role", "role"),)

    tasks: Mapped[list["EvalTask"]] = relationship(back_populates="user")


# --------------------------------------------------------------------------- #
# 知识库（FR-KB，SRS 5.2 spec_doc / doc_version / doc_chunk）
# --------------------------------------------------------------------------- #
class KnowledgeBase(Base, TimestampMixin):
    __tablename__ = "knowledge_base"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    specialty: Mapped[Optional[str]] = mapped_column(String(64))
    description: Mapped[Optional[str]] = mapped_column(Text)
    retrieval_config: Mapped[Optional[dict]] = mapped_column(JSONType)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    #: 归属项目。为空表示「公共知识库」（所有用户可读）；有值则仅该项目成员可访问。
    #: 访问控制见 app/core/authz.py。
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("project.id"))
    #: 创建者。用于让上传者始终可见自己建的知识库。
    owner_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sys_user.id"))


class SpecDoc(Base, TimestampMixin):
    """规范文档主表（SRS 5.2 spec_doc）。"""

    __tablename__ = "spec_doc"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    kb_id: Mapped[Optional[int]] = mapped_column(ForeignKey("knowledge_base.id"))
    spec_code: Mapped[str] = mapped_column(String(64), nullable=False)
    spec_name: Mapped[str] = mapped_column(String(255), nullable=False)
    issuer: Mapped[Optional[str]] = mapped_column(String(128))
    publish_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    effective_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    abolish_date: Mapped[Optional[dt.date]] = mapped_column(Date)
    specialty: Mapped[Optional[str]] = mapped_column(String(64))
    region_level: Mapped[Optional[str]] = mapped_column(String(32), default="national")
    scope: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default=DocStatus.DRAFT.value, nullable=False)
    uploader_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sys_user.id"))
    remark: Mapped[Optional[str]] = mapped_column(Text)
    kg_built: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        UniqueConstraint("spec_code", "specialty", name="uq_spec_code_specialty"),
        Index("ix_spec_doc_status", "status"),
        Index("ix_spec_doc_specialty", "specialty"),
    )

    versions: Mapped[list["DocVersion"]] = relationship(
        back_populates="doc", cascade="all, delete-orphan"
    )


class DocVersion(Base, TimestampMixin):
    """文档版本（FR-KB-07）。"""

    __tablename__ = "doc_version"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_id: Mapped[int] = mapped_column(ForeignKey("spec_doc.id", ondelete="CASCADE"), nullable=False)
    version_label: Mapped[str] = mapped_column(String(64), nullable=False)
    file_name: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_size: Mapped[Optional[int]] = mapped_column(BigInteger)
    file_hash: Mapped[Optional[str]] = mapped_column(String(64))
    file_type: Mapped[Optional[str]] = mapped_column(String(16))
    page_count: Mapped[Optional[int]] = mapped_column(Integer)
    parse_status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)
    parse_error: Mapped[Optional[str]] = mapped_column(Text)
    is_current: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    chunk_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    parse_meta: Mapped[Optional[dict]] = mapped_column(JSONType)

    __table_args__ = (Index("ix_doc_version_doc", "doc_id", "is_current"),)

    doc: Mapped["SpecDoc"] = relationship(back_populates="versions")
    chunks: Mapped[list["DocChunk"]] = relationship(
        back_populates="version", cascade="all, delete-orphan"
    )


class DocChunk(Base, TimestampMixin):
    """文本块（SRS 5.2 doc_chunk）。"""

    __tablename__ = "doc_chunk"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    doc_version_id: Mapped[int] = mapped_column(
        ForeignKey("doc_version.id", ondelete="CASCADE"), nullable=False
    )
    doc_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    clause_no: Mapped[Optional[str]] = mapped_column(String(64))
    chapter_path: Mapped[Optional[str]] = mapped_column(String(512))
    chunk_index: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    page_no: Mapped[Optional[int]] = mapped_column(Integer)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    token_count: Mapped[Optional[int]] = mapped_column(Integer)
    faiss_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    embedding_model: Mapped[Optional[str]] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16), default=ChunkStatus.ACTIVE.value)
    extra: Mapped[Optional[dict]] = mapped_column(JSONType)

    __table_args__ = (
        Index("ix_doc_chunk_clause", "clause_no"),
        Index("ix_doc_chunk_faiss", "faiss_id"),
        Index("ix_doc_chunk_doc", "doc_id", "status"),
    )

    version: Mapped["DocVersion"] = relationship(back_populates="chunks")


class ClauseRef(Base, TimestampMixin):
    """条款引用关系（FR-KG-04），与 Neo4j 图谱互为正/反向索引。"""

    __tablename__ = "clause_ref"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    src_clause_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    src_clause_no: Mapped[Optional[str]] = mapped_column(String(64))
    dst_clause_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    dst_clause_no: Mapped[Optional[str]] = mapped_column(String(64))
    dst_spec_code: Mapped[Optional[str]] = mapped_column(String(64))
    relation: Mapped[str] = mapped_column(String(32), nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Float)
    source: Mapped[str] = mapped_column(String(16), default="llm")
    evidence: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (
        Index("ix_clause_ref_src", "src_clause_id", "relation"),
        Index("ix_clause_ref_dst", "dst_clause_no"),
    )


# --------------------------------------------------------------------------- #
# 评估任务（FR-AGT，SRS 5.2 eval_task / subtask / match_result）
# --------------------------------------------------------------------------- #
class EvalTask(Base, TimestampMixin):
    __tablename__ = "eval_task"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    project_id: Mapped[Optional[int]] = mapped_column(ForeignKey("project.id"))
    user_id: Mapped[Optional[int]] = mapped_column(ForeignKey("sys_user.id"))
    eval_type: Mapped[str] = mapped_column(String(32), default="inspection_lot", nullable=False)
    specialty: Mapped[Optional[str]] = mapped_column(String(64))
    title: Mapped[Optional[str]] = mapped_column(String(255))
    input_payload: Mapped[dict] = mapped_column(JSONType, default=dict, nullable=False)
    options: Mapped[Optional[dict]] = mapped_column(JSONType)
    current_state: Mapped[str] = mapped_column(
        String(24), default=EvalState.PENDING.value, nullable=False
    )
    #: 乐观锁版本号：人工裁定提交时带上期望版本，防止两人同时裁定同一任务。
    #: 版本不匹配返回 409，提示刷新后重试（见 app/services/review.py）。
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    current_step: Mapped[Optional[str]] = mapped_column(String(32))
    iteration_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    no_progress_rounds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    progress: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    state_snapshot: Mapped[Optional[dict]] = mapped_column(JSONType)
    error_state: Mapped[Optional[dict]] = mapped_column(JSONType)
    guard_reason: Mapped[Optional[str]] = mapped_column(Text)
    checkpoint_thread_id: Mapped[Optional[str]] = mapped_column(String(64))
    started_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_cost: Mapped[Optional[float]] = mapped_column(Numeric(12, 6), default=0)

    __table_args__ = (
        Index("ix_eval_task_state", "current_state"),
        Index("ix_eval_task_user", "user_id", "created_at"),
        Index("ix_eval_task_project", "project_id"),
    )

    project: Mapped[Optional["Project"]] = relationship(back_populates="tasks")
    user: Mapped[Optional["User"]] = relationship(back_populates="tasks")
    subtasks: Mapped[list["EvalSubtask"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    matches: Mapped[list["MatchResult"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    report: Mapped[Optional["EvalReport"]] = relationship(
        back_populates="task", uselist=False, cascade="all, delete-orphan"
    )
    steps: Mapped[list["AgentStepLog"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )
    tool_calls: Mapped[list["AgentToolCall"]] = relationship(
        back_populates="task", cascade="all, delete-orphan"
    )


class EvalSubtask(Base, TimestampMixin):
    """任务拆解产物（FR-AGT-03）。"""

    __tablename__ = "eval_subtask"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("eval_task.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    criterion: Mapped[Optional[str]] = mapped_column(Text)
    required_evidence: Mapped[Optional[str]] = mapped_column(Text)
    specialty: Mapped[Optional[str]] = mapped_column(String(64))
    query: Mapped[Optional[str]] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), default="pending", nullable=False)

    __table_args__ = (Index("ix_eval_subtask_task", "task_id", "seq"),)

    task: Mapped["EvalTask"] = relationship(back_populates="subtasks")


class MatchResult(Base, TimestampMixin):
    """条款匹配结果（FR-AGT-05，SRS 5.2 match_result）。"""

    __tablename__ = "match_result"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("eval_task.id", ondelete="CASCADE"), nullable=False
    )
    subtask_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    clause_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    clause_no: Mapped[Optional[str]] = mapped_column(String(64))
    spec_code: Mapped[Optional[str]] = mapped_column(String(64))
    spec_name: Mapped[Optional[str]] = mapped_column(String(255))
    verdict: Mapped[str] = mapped_column(String(24), nullable=False)
    confidence: Mapped[Optional[float]] = mapped_column(Numeric(4, 3))
    relevance_score: Mapped[Optional[float]] = mapped_column(Numeric(6, 4))
    evidence: Mapped[Optional[str]] = mapped_column(Text)
    reasoning: Mapped[Optional[str]] = mapped_column(Text)
    risk_level: Mapped[Optional[str]] = mapped_column(String(8))
    remediation: Mapped[Optional[str]] = mapped_column(Text)
    citation_json: Mapped[Optional[dict]] = mapped_column(JSONType)

    __table_args__ = (
        Index("ix_match_result_task", "task_id"),
        Index("ix_match_result_verdict", "verdict"),
    )

    task: Mapped["EvalTask"] = relationship(back_populates="matches")


class AgentStepLog(Base, TimestampMixin):
    """Agent 执行轨迹（FR-AGT-13）。"""

    __tablename__ = "agent_step_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("eval_task.id", ondelete="CASCADE"), nullable=False
    )
    step: Mapped[str] = mapped_column(String(32), nullable=False)
    seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    token_used: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    iteration: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    output_digest: Mapped[Optional[dict]] = mapped_column(JSONType)
    error: Mapped[Optional[str]] = mapped_column(Text)

    __table_args__ = (Index("ix_agent_step_task", "task_id", "seq"),)

    task: Mapped["EvalTask"] = relationship(back_populates="steps")


class AgentToolCall(Base, TimestampMixin):
    """Agent 工具调用审计（对应权限设计的「工具调用权限」）。

    与 ``agent_step_log`` 的区别：后者是**五阶段流程摘要**（且落库时会先删除重写，
    用于断点续跑重放）；本表是**工具调用的不可变审计流水**，记录每次调用是否放行、
    拒绝原因与参数摘要，用于事后追溯「谁在何时调用了什么、是否越权」。
    """

    __tablename__ = "agent_tool_call"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("eval_task.id", ondelete="CASCADE"), nullable=False
    )
    seq: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    tool: Mapped[str] = mapped_column(String(64), nullable=False)
    #: 是否放行
    allowed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    #: 调用者（发起评估的用户）
    user_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("sys_user.id", ondelete="CASCADE")
    )
    #: 参数摘要（长文本已截断）
    params: Mapped[Optional[dict]] = mapped_column(JSONType)
    #: 拒绝原因或错误信息
    reason: Mapped[Optional[str]] = mapped_column(Text)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer)
    output_digest: Mapped[Optional[str]] = mapped_column(String(255))

    __table_args__ = (Index("ix_agent_tool_task", "task_id", "seq"),)

    task: Mapped["EvalTask"] = relationship(back_populates="tool_calls")


class EvalReport(Base, TimestampMixin):
    """评估报告（FR-AGT-07）。"""

    __tablename__ = "eval_report"

    id: Mapped[uuid.UUID] = mapped_column(GUID, primary_key=True, default=uuid.uuid4)
    task_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("eval_task.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    conclusion: Mapped[Optional[str]] = mapped_column(Text)
    overall_verdict: Mapped[Optional[str]] = mapped_column(String(24))
    risk_level: Mapped[Optional[str]] = mapped_column(String(8))
    summary: Mapped[Optional[str]] = mapped_column(Text)
    content: Mapped[dict] = mapped_column(JSONType, nullable=False)
    markdown: Mapped[Optional[str]] = mapped_column(Text)
    basis_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    non_compliance_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    generator_model: Mapped[Optional[str]] = mapped_column(String(64))
    # ---- 人工复核裁定（SRS 角色定义「复核与裁定、终审签发」）----
    #: 人工裁定结论（HumanVerdict）。与 overall_verdict（机器结论）**并存**，
    #: 不可互相覆盖：机器结论是 AI 质量的原始证据，人工裁定是责任判定。
    #: 为空表示「尚未经人工裁定」—— 存量历史报告均为空，不要批量回填。
    human_verdict: Mapped[Optional[str]] = mapped_column(String(16))
    #: 裁定依据/说明（与逐条条款修订说明分开记录）
    review_comment: Mapped[Optional[str]] = mapped_column(Text)
    #: 最近一次签认人
    reviewed_by: Mapped[Optional[int]] = mapped_column(BigInteger)
    #: 最近一次签认时间
    reviewed_at: Mapped[Optional[dt.datetime]] = mapped_column(DateTime(timezone=True))
    #: 是否已签发生效（判定合格时为 True）。False = 草稿，不得作为正式依据。
    is_final: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    task: Mapped["EvalTask"] = relationship(back_populates="report")
    scores: Mapped[list["JudgeScore"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )
    reviews: Mapped[list["JudgeReview"]] = relationship(
        back_populates="report", cascade="all, delete-orphan"
    )


# --------------------------------------------------------------------------- #
# Judge 评审（FR-JDG，SRS 5.2 judge_score）
# --------------------------------------------------------------------------- #
class JudgeReview(Base, TimestampMixin):
    """一次评审（可含多模型），汇总总分与拦截决策。"""

    __tablename__ = "judge_review"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("eval_report.id", ondelete="CASCADE"), nullable=False
    )
    judge_model: Mapped[Optional[str]] = mapped_column(String(64))
    total_score: Mapped[Optional[float]] = mapped_column(Numeric(3, 2))
    grade: Mapped[Optional[str]] = mapped_column(String(16))
    weights: Mapped[Optional[dict]] = mapped_column(JSONType)
    citation_check: Mapped[Optional[dict]] = mapped_column(JSONType)
    has_conflict: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    needs_human: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    threshold: Mapped[Optional[float]] = mapped_column(Float)
    comment: Mapped[Optional[str]] = mapped_column(Text)
    raw_output: Mapped[Optional[dict]] = mapped_column(JSONType)

    __table_args__ = (Index("ix_judge_review_report", "report_id"),)

    report: Mapped["EvalReport"] = relationship(back_populates="reviews")
    scores: Mapped[list["JudgeScore"]] = relationship(
        back_populates="review", cascade="all, delete-orphan"
    )


class JudgeScore(Base, TimestampMixin):
    """维度评分（SRS 5.2 judge_score）。"""

    __tablename__ = "judge_score"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    report_id: Mapped[uuid.UUID] = mapped_column(
        GUID, ForeignKey("eval_report.id", ondelete="CASCADE"), nullable=False
    )
    review_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("judge_review.id", ondelete="CASCADE")
    )
    judge_model: Mapped[Optional[str]] = mapped_column(String(64))
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    score: Mapped[Optional[float]] = mapped_column(Numeric(3, 1))
    weight: Mapped[Optional[float]] = mapped_column(Numeric(4, 3))
    comment: Mapped[Optional[str]] = mapped_column(Text)
    is_conflict: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)

    __table_args__ = (
        Index("ix_judge_score_report", "report_id"),
        Index("ix_judge_score_dim", "dimension"),
    )

    report: Mapped["EvalReport"] = relationship(back_populates="scores")
    review: Mapped[Optional["JudgeReview"]] = relationship(back_populates="scores")


class HumanFeedback(Base, TimestampMixin):
    """专家复核反馈（FR-JDG-06 反馈闭环）。"""

    __tablename__ = "human_feedback"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID)
    report_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    dimension: Mapped[Optional[str]] = mapped_column(String(32))
    original_value: Mapped[Optional[dict]] = mapped_column(JSONType)
    corrected_value: Mapped[Optional[dict]] = mapped_column(JSONType)
    comment: Mapped[Optional[str]] = mapped_column(Text)


# --------------------------------------------------------------------------- #
# 可观测与审计（FR-SYS-03/06）
# --------------------------------------------------------------------------- #
class AuditLog(Base):
    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String(64))
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger)
    username: Mapped[Optional[str]] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    object_type: Mapped[Optional[str]] = mapped_column(String(32))
    object_id: Mapped[Optional[str]] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(16), default="success", nullable=False)
    ip: Mapped[Optional[str]] = mapped_column(String(64))
    user_agent: Mapped[Optional[str]] = mapped_column(String(255))
    detail: Mapped[Optional[dict]] = mapped_column(JSONType)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )

    __table_args__ = (
        Index("ix_audit_log_user", "user_id", "created_at"),
        Index("ix_audit_log_action", "action"),
    )


class LlmCallLog(Base):
    """LLM 调用日志（SRS 7.4 可观测性：Token 消耗）。"""

    __tablename__ = "llm_call_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    trace_id: Mapped[Optional[str]] = mapped_column(String(64))
    task_id: Mapped[Optional[uuid.UUID]] = mapped_column(GUID)
    scene: Mapped[Optional[str]] = mapped_column(String(32))
    provider: Mapped[Optional[str]] = mapped_column(String(32))
    model: Mapped[Optional[str]] = mapped_column(String(64))
    is_fallback: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    prompt_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    completion_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    total_tokens: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer)
    success: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    error: Mapped[Optional[str]] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime(timezone=True), default=_now, server_default=func.now(), nullable=False
    )


class SystemConfig(Base, TimestampMixin):
    """运行期可调配置（FR-SYS-04/05）。"""

    __tablename__ = "system_config"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    value: Mapped[Optional[dict]] = mapped_column(JSONType)
    category: Mapped[Optional[str]] = mapped_column(String(32))
    description: Mapped[Optional[str]] = mapped_column(Text)
    updated_by: Mapped[Optional[int]] = mapped_column(BigInteger)


__all__ = [
    "Base",
    "Project",
    "User",
    "KnowledgeBase",
    "SpecDoc",
    "DocVersion",
    "DocChunk",
    "ClauseRef",
    "EvalTask",
    "EvalSubtask",
    "MatchResult",
    "AgentStepLog",
    "EvalReport",
    "JudgeReview",
    "JudgeScore",
    "HumanFeedback",
    "AuditLog",
    "LlmCallLog",
    "SystemConfig",
]
