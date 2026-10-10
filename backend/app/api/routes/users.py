# -*- coding: utf-8 -*-
"""用户与项目管理接口（补齐访问控制的运维闭环）。

为什么需要
----------
访问控制依赖 ``sys_user.project_ids``（用户被授权访问的项目），
但此前**没有任何接口可以配置它** —— 只能直接改数据库。
这导致新用户创建后「看不到任何项目数据」，而管理员无从下手。

本模块提供最小可用的管理能力：

- ``GET  /admin/projects``            项目列表（用于配置时的下拉）
- ``GET  /admin/users``               用户列表（含 project_ids 概览）
- ``POST /admin/users``               创建用户（可同时指定项目与角色）
- ``POST /admin/users/{id}/projects`` 更新用户的项目授权
- ``POST /admin/users/{id}/status``   启用/停用

仅管理员可访问（``admin:*``）。
"""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Body, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbSession, client_ip, require_permission
from app.constants import AuditAction
from app.core.authz import ADMIN_ROLES, project_ids_of
from app.core.errors import ConflictError, NotFoundError, ParamInvalidError
from app.core.logging_conf import get_logger
from app.core.response import ok, paginate
from app.core.security import hash_password
from app.db import add_audit_log
from app.db.models import Project, User

router = APIRouter(tags=["用户与项目"])
logger = get_logger(__name__)

#: 允许分配的角色（与 deps.ROLE_PERMISSIONS 的键保持一致）
ASSIGNABLE_ROLES = frozenset({"admin", "kb_manager", "engineer", "expert", "viewer"})


class UserCreateRequest(BaseModel):
    username: str = Field(min_length=2, max_length=64)
    password: str = Field(min_length=8, max_length=128)
    # ---- 人员身份（账号与身份分离）----
    full_name: Optional[str] = Field(default=None, max_length=64)
    #: 工号：单位内部唯一，用于与人事/项目台账对齐
    employee_no: Optional[str] = Field(default=None, max_length=64)
    org_name: Optional[str] = Field(default=None, max_length=128)
    department: Optional[str] = Field(default=None, max_length=128)
    position: Optional[str] = Field(default=None, max_length=64)
    cert_no: Optional[str] = Field(default=None, max_length=64)
    signature: Optional[str] = Field(default=None, max_length=64)
    email: Optional[str] = Field(default=None, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=32)
    role: str = Field(default="engineer", max_length=32)
    #: 授权访问的项目 ID 列表。非管理员用户必须至少有一个，否则看不到任何项目数据。
    project_ids: list[int] = Field(default_factory=list)
    specialties: list[str] = Field(default_factory=list)


class ProjectsUpdateRequest(BaseModel):
    project_ids: list[int] = Field(default_factory=list)


class StatusUpdateRequest(BaseModel):
    is_active: bool


class UserUpdateRequest(BaseModel):
    """更新用户资料与角色（全部为可选，只改传入的字段）。

    ``role`` 变更会**立即生效**（权限由服务端角色派生），因此审计日志
    必须记录角色前后值 —— 权限变更是最需要留痕的操作之一。
    """

    # ---- 人员身份 ----
    full_name: Optional[str] = Field(default=None, max_length=64)
    employee_no: Optional[str] = Field(default=None, max_length=64)
    org_name: Optional[str] = Field(default=None, max_length=128)
    department: Optional[str] = Field(default=None, max_length=128)
    position: Optional[str] = Field(default=None, max_length=64)
    cert_no: Optional[str] = Field(default=None, max_length=64)
    signature: Optional[str] = Field(default=None, max_length=64)
    email: Optional[str] = Field(default=None, max_length=128)
    phone: Optional[str] = Field(default=None, max_length=32)
    role: Optional[str] = Field(default=None, max_length=32)
    specialties: Optional[list[str]] = None
    project_ids: Optional[list[int]] = None


class PasswordResetRequest(BaseModel):
    """管理员重置用户密码。

    ⚠️ 本系统**没有自助找回密码**能力（企业内网系统的常规做法是找管理员）。
    因此重置密码是管理员的核心职责之一 —— 否则用户忘记密码后无法恢复。
    """

    new_password: str = Field(min_length=8, max_length=128)
    #: 是否要求该用户下次登录后修改（默认要求，与「首次登录须改初始密码」一致）
    must_change: bool = True


def _user_out(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        # ---- 人员身份（账号与身份分离）----
        "full_name": user.full_name,
        "employee_no": getattr(user, "employee_no", None),
        "org_name": getattr(user, "org_name", None),
        "department": getattr(user, "department", None),
        "position": getattr(user, "position", None),
        "cert_no": getattr(user, "cert_no", None),
        "signature": getattr(user, "signature", None),
        "email": user.email,
        "phone": user.phone,
        "role": user.role,
        "project_ids": sorted(project_ids_of(user)),
        "specialties": list(user.specialties or []),
        "is_active": user.is_active,
        "must_change_password": bool(getattr(user, "must_change_password", False)),
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
    }


#: 可更新的身份字段（与 UserUpdateRequest 对应，集中声明避免漏改）
IDENTITY_FIELDS: tuple[str, ...] = (
    "full_name",
    "employee_no",
    "org_name",
    "department",
    "position",
    "cert_no",
    "signature",
    "email",
    "phone",
)


def _validate_projects(session, project_ids: list[int]) -> list[int]:
    """校验项目存在，返回去重排序后的 ID 列表。"""
    wanted = sorted({int(x) for x in project_ids})
    if not wanted:
        return []
    rows = session.execute(select(Project.id).where(Project.id.in_(wanted))).scalars().all()
    existing = {int(x) for x in rows}
    missing = [x for x in wanted if x not in existing]
    if missing:
        raise ParamInvalidError(f"项目不存在：{missing}")
    return wanted


# --------------------------------------------------------------------------- #
# 项目
# --------------------------------------------------------------------------- #
@router.get("/admin/projects", summary="项目列表（用于用户授权下拉）")
def list_projects(
    session: DbSession,
    user: Annotated[User, require_permission("admin:*")],
) -> dict:
    rows = session.execute(select(Project).order_by(Project.id)).scalars().all()
    return ok(
        [
            {
                "id": p.id,
                "code": p.code,
                "name": p.name,
                "specialty": p.specialty,
                "status": p.status,
            }
            for p in rows
        ]
    )


# --------------------------------------------------------------------------- #
# 用户
# --------------------------------------------------------------------------- #
@router.get("/admin/users", summary="用户列表")
def list_users(
    session: DbSession,
    user: Annotated[User, require_permission("admin:*")],
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    keyword: Optional[str] = Query(None, description="按用户名/姓名模糊筛选"),
) -> dict:
    stmt = select(User)
    if keyword:
        like = f"%{keyword.strip()}%"
        stmt = stmt.where(User.username.ilike(like) | User.full_name.ilike(like))
    stmt = stmt.order_by(User.id)

    total = int(session.execute(select(func.count()).select_from(stmt.subquery())).scalar() or 0)
    rows = session.execute(stmt.limit(page_size).offset((page - 1) * page_size)).scalars().all()
    return ok(paginate([_user_out(u) for u in rows], total, page, page_size))


@router.post("/admin/users", summary="创建用户（含项目授权）")
def create_user(
    request: Request,
    session: DbSession,
    user: Annotated[User, require_permission("admin:*")],
    body: UserCreateRequest = Body(...),
) -> dict:
    role = (body.role or "").strip().lower()
    if role not in ASSIGNABLE_ROLES:
        raise ParamInvalidError(f"角色非法：{body.role}，可选 {sorted(ASSIGNABLE_ROLES)}")

    exists = session.execute(
        select(User).where(User.username == body.username.strip())
    ).scalars().first()
    if exists is not None:
        raise ConflictError(f"用户名已存在：{body.username}")

    project_ids = _validate_projects(session, body.project_ids)
    # 非管理员没有授权项目会导致「登录后什么都看不到」，提前拦住并说明原因
    if role not in ADMIN_ROLES and not project_ids:
        raise ParamInvalidError(
            "非管理员用户必须至少授权一个项目，否则登录后看不到任何项目数据"
            "（仅公共规范库可见）。请指定 project_ids。"
        )

    # 工号唯一（数据库有部分唯一索引；这里先给出可读错误，避免 500）
    employee_no = (body.employee_no or "").strip() or None
    if employee_no:
        dup = session.execute(
            select(User).where(User.employee_no == employee_no)
        ).scalars().first()
        if dup is not None:
            raise ConflictError(f"工号已被占用：{employee_no}（用户 {dup.username}）")

    created = User(
        username=body.username.strip(),
        # ---- 人员身份（账号与身份分离）----
        full_name=body.full_name,
        employee_no=employee_no,
        org_name=body.org_name,
        department=body.department,
        position=body.position,
        cert_no=body.cert_no,
        signature=body.signature,
        email=body.email,
        phone=body.phone,
        password_hash=hash_password(body.password),
        role=role,
        project_ids=project_ids,
        specialties=body.specialties,
    )
    session.add(created)
    session.flush()

    add_audit_log(
        session,
        AuditAction.CONFIG_CHANGE.value,
        user_id=user.id,
        username=user.username,
        ip=client_ip(request),
        object_type="sys_user",
        object_id=created.id,
        detail={"action": "create", "role": role, "project_ids": project_ids},
    )
    session.commit()
    return ok(_user_out(created))


@router.post("/admin/users/{user_id}/projects", summary="更新用户的项目授权")
def update_user_projects(
    user_id: int,
    request: Request,
    session: DbSession,
    user: Annotated[User, require_permission("admin:*")],
    body: ProjectsUpdateRequest = Body(...),
) -> dict:
    target = session.get(User, user_id)
    if target is None:
        raise NotFoundError(f"用户不存在：{user_id}")

    project_ids = _validate_projects(session, body.project_ids)
    if str(target.role).lower() not in ADMIN_ROLES and not project_ids:
        raise ParamInvalidError(
            "非管理员用户必须至少授权一个项目，否则将看不到任何项目数据"
        )

    before = sorted(project_ids_of(target))
    target.project_ids = project_ids
    session.flush()

    add_audit_log(
        session,
        AuditAction.CONFIG_CHANGE.value,
        user_id=user.id,
        username=user.username,
        ip=client_ip(request),
        object_type="sys_user",
        object_id=target.id,
        detail={"action": "update_projects", "before": before, "after": project_ids},
    )
    session.commit()
    logger.info(
        "用户项目授权已更新",
        extra={"target_user": target.username, "before": before, "after": project_ids},
    )
    return ok(_user_out(target))


@router.post("/admin/users/{user_id}/status", summary="启用/停用用户")
def update_user_status(
    user_id: int,
    request: Request,
    session: DbSession,
    user: Annotated[User, require_permission("admin:*")],
    body: StatusUpdateRequest = Body(...),
) -> dict:
    target = session.get(User, user_id)
    if target is None:
        raise NotFoundError(f"用户不存在：{user_id}")
    if target.id == getattr(user, "id", None) and not body.is_active:
        raise ParamInvalidError("不能停用当前登录账号")

    target.is_active = bool(body.is_active)
    session.flush()
    add_audit_log(
        session,
        AuditAction.CONFIG_CHANGE.value,
        user_id=user.id,
        username=user.username,
        ip=client_ip(request),
        object_type="sys_user",
        object_id=target.id,
        detail={"action": "set_active", "is_active": target.is_active},
    )
    session.commit()
    return ok(_user_out(target))


@router.get("/admin/users/{user_id}", summary="查询单个用户详情")
def get_user(
    user_id: int,
    session: DbSession,
    user: Annotated[User, require_permission("admin:*")],
) -> dict:
    """查询单个用户（含授权项目与最近登录时间），供编辑表单回填。"""
    target = session.get(User, user_id)
    if target is None:
        raise NotFoundError(f"用户不存在：{user_id}")
    return ok(_user_out(target))


@router.patch("/admin/users/{user_id}", summary="更新用户资料与角色")
def update_user(
    user_id: int,
    request: Request,
    session: DbSession,
    user: Annotated[User, require_permission("admin:*")],
    body: UserUpdateRequest = Body(...),
) -> dict:
    """更新用户资料与角色（只改传入的字段）。

    **角色变更立即生效**：权限由服务端角色派生（见 ``deps.ROLE_PERMISSIONS``），
    因此审计日志记录角色前后值 —— 权限变更是最需要留痕的操作。
    """
    target = session.get(User, user_id)
    if target is None:
        raise NotFoundError(f"用户不存在：{user_id}")

    changes: dict[str, object] = {}
    is_self = target.id == getattr(user, "id", None)

    # ---- 人员身份字段 ----
    # 工号需唯一校验（数据库有部分唯一索引，这里先给出可读的错误提示）
    if body.employee_no is not None and body.employee_no != target.employee_no:
        if body.employee_no.strip():
            dup = session.execute(
                select(User).where(
                    User.employee_no == body.employee_no.strip(), User.id != target.id
                )
            ).scalars().first()
            if dup is not None:
                raise ConflictError(f"工号已被占用：{body.employee_no}（用户 {dup.username}）")

    for field in IDENTITY_FIELDS:
        value = getattr(body, field)
        if value is None:
            continue
        # 工号统一去空白，避免「001 」与「001」被视为不同工号
        if field == "employee_no":
            value = value.strip() or None
        if value != getattr(target, field):
            changes[field] = {"before": getattr(target, field), "after": value}
            setattr(target, field, value)

    if body.specialties is not None:
        changes["specialties"] = {"before": list(target.specialties or []), "after": body.specialties}
        target.specialties = body.specialties

    # ---- 角色变更 ----
    if body.role is not None:
        new_role = str(body.role).strip().lower()
        if new_role not in ASSIGNABLE_ROLES:
            raise ParamInvalidError(
                f"角色必须是 {sorted(ASSIGNABLE_ROLES)} 之一，收到：{body.role!r}"
            )
        old_role = str(target.role).lower()
        if new_role != old_role:
            # 不允许管理员改自己的角色：会把自己降权后无法恢复（需改库）
            if is_self:
                raise ParamInvalidError("不能修改当前登录账号的角色，请由其他管理员操作")
            changes["role"] = {"before": old_role, "after": new_role}
            target.role = new_role

    # ---- 项目授权（与角色联动校验）----
    if body.project_ids is not None:
        project_ids = _validate_projects(session, body.project_ids)
        effective_role = str(target.role).lower()
        if effective_role not in ADMIN_ROLES and not project_ids:
            raise ParamInvalidError(
                "非管理员用户必须至少授权一个项目，否则登录后看不到任何项目数据"
            )
        before = sorted(project_ids_of(target))
        if before != project_ids:
            changes["project_ids"] = {"before": before, "after": project_ids}
            target.project_ids = project_ids

    if not changes:
        return ok(_user_out(target))

    session.flush()
    add_audit_log(
        session,
        AuditAction.CONFIG_CHANGE.value,
        user_id=user.id,
        username=user.username,
        ip=client_ip(request),
        object_type="sys_user",
        object_id=target.id,
        detail={"action": "update", "changes": changes},
    )
    session.commit()
    logger.info("用户信息已更新", extra={"target_user": target.username, "fields": list(changes)})
    return ok(_user_out(target))


@router.post("/admin/users/{user_id}/password", summary="重置用户密码")
def reset_user_password(
    user_id: int,
    request: Request,
    session: DbSession,
    user: Annotated[User, require_permission("admin:*")],
    body: PasswordResetRequest = Body(...),
) -> dict:
    """管理员重置用户密码。

    ⚠️ 本系统**没有自助找回密码**能力（企业内网系统的常规做法是联系管理员），
    因此这是用户忘记密码后唯一的恢复途径。

    审计日志**只记录「已重置」这一事实，绝不记录密码内容** ——
    即使密码是加密存储的，审计表也不应出现凭证。
    """
    target = session.get(User, user_id)
    if target is None:
        raise NotFoundError(f"用户不存在：{user_id}")

    target.password_hash = hash_password(body.new_password)
    target.must_change_password = bool(body.must_change)
    session.flush()

    add_audit_log(
        session,
        AuditAction.CONFIG_CHANGE.value,
        user_id=user.id,
        username=user.username,
        ip=client_ip(request),
        object_type="sys_user",
        object_id=target.id,
        # 只记录事实与策略，不记录密码
        detail={"action": "reset_password", "must_change": bool(body.must_change)},
    )
    session.commit()
    logger.info("用户密码已重置", extra={"target_user": target.username})
    return ok({"user_id": target.id, "username": target.username, "must_change_password": target.must_change_password})


@router.delete("/admin/users/{user_id}", summary="删除用户（默认停用，hard=true 且无业务数据才真删）")
def delete_user(
    user_id: int,
    request: Request,
    session: DbSession,
    user: Annotated[User, require_permission("admin:*")],
    hard: bool = Query(False, description="true=物理删除（要求无业务数据）；默认停用（软删除）"),
) -> dict:
    """删除用户。

    **默认是软删除（停用）**，理由：

    1. ``eval_task.user_id`` / ``spec_doc.uploader_id`` / ``knowledge_base.owner_id``
       都指向 ``sys_user``，物理删除有业务数据的用户会被外键拦下（或需级联删掉
       评估报告与规范文档 —— 那是不可接受的）；
    2. 评估结论需要**可追溯责任人**，删掉账号会让「谁发起的评估」永久丢失，
       违背 FR-SYS-03 的审计要求。

    ``hard=true`` 时先检查是否有关联业务数据；有则拒绝并提示改用停用 ——
    这既满足「清理误建账号」的需求，又不会静默破坏审计链。
    """
    target = session.get(User, user_id)
    if target is None:
        raise NotFoundError(f"用户不存在：{user_id}")

    if target.id == getattr(user, "id", None):
        raise ParamInvalidError("不能删除当前登录账号")
    if str(target.role).lower() in ADMIN_ROLES and _active_admin_count(session) <= 1:
        raise ParamInvalidError("系统必须保留至少一个启用状态的管理员，不能删除最后一个")

    if not hard:
        # 软删除：停用即可。历史记录与审计链完整保留。
        target.is_active = False
        session.flush()
        add_audit_log(
            session,
            AuditAction.CONFIG_CHANGE.value,
            user_id=user.id,
            username=user.username,
            ip=client_ip(request),
            object_type="sys_user",
            object_id=target.id,
            detail={"action": "deactivate_on_delete", "mode": "soft"},
        )
        session.commit()
        logger.info("用户已停用（软删除）", extra={"target_user": target.username})
        return ok(
            {
                "user_id": target.id,
                "username": target.username,
                "mode": "deactivated",
                "message": "已停用该账号。历史评估记录与审计留痕保留，可随时重新启用。",
            }
        )

    # ---- 物理删除：先查关联业务数据 ----
    blockers = _blocking_references(session, target.id)
    if blockers:
        detail = "、".join(f"{name} {count} 条" for name, count in blockers.items())
        raise ConflictError(
            f"该用户存在关联业务数据（{detail}），物理删除会破坏审计链或需级联删除评估记录。"
            "请改用停用（不传 hard 参数），或先转移/清理这些数据。"
        )

    username = target.username
    session.delete(target)
    session.flush()
    add_audit_log(
        session,
        AuditAction.CONFIG_CHANGE.value,
        user_id=user.id,
        username=user.username,
        ip=client_ip(request),
        object_type="sys_user",
        object_id=user_id,
        detail={"action": "delete", "mode": "hard", "deleted_username": username},
    )
    session.commit()
    logger.warning("用户已物理删除", extra={"target_user": username, "by": user.username})
    return ok({"user_id": user_id, "username": username, "mode": "deleted", "message": "用户已删除"})


def _blocking_references(session, user_id: int) -> dict[str, int]:
    """统计会阻止物理删除的关联业务数据。

    只统计**必须保留**的三类：发起的评估任务、上传的规范文档、创建的知识库。
    其他表（如 audit_log / human_feedback）通过置空 ``user_id`` 即可保留留痕。
    """
    from app.db.models import EvalTask, KnowledgeBase, SpecDoc

    result: dict[str, int] = {}
    for label, model, column in (
        ("评估任务", EvalTask, EvalTask.user_id),
        ("规范文档", SpecDoc, SpecDoc.uploader_id),
        ("知识库", KnowledgeBase, KnowledgeBase.owner_id),
    ):
        count = int(session.execute(select(func.count()).where(column == user_id)).scalar() or 0)
        if count:
            result[label] = count
    return result


def _active_admin_count(session) -> int:
    """启用状态的管理员数量（用于「不能删最后一个管理员」的保护）。"""
    stmt = select(func.count()).where(User.role.in_(sorted(ADMIN_ROLES)), User.is_active.is_(True))
    return int(session.execute(stmt).scalar() or 0)


@router.get("/me/scope", summary="当前用户的访问范围（用于前端提示）")
def my_scope(session: DbSession, user: CurrentUser) -> dict:
    """返回当前用户的角色、授权项目与可访问知识库数量。

    前端可用它提示「你尚未被授权任何项目」，避免用户困惑于「看不到数据」。
    """
    from app.core.authz import accessible_kb_ids, is_admin, user_id_of

    project_ids = sorted(project_ids_of(user))
    allowed_kbs = accessible_kb_ids(session, user)
    return ok(
        {
            "user_id": user_id_of(user),
            "username": user.username,
            "role": user.role,
            "is_admin": is_admin(user),
            "project_ids": project_ids,
            "accessible_kb_count": None if allowed_kbs is None else len(allowed_kbs),
            # 关键提示：非管理员且无项目授权时，列表类接口只会返回自己创建的数据
            "warning": (
                None
                if (is_admin(user) or project_ids)
                else "你尚未被授权任何项目，只能看到自己发起的数据与公共规范库。"
                "请联系管理员在「用户管理」中为你分配项目。"
            ),
        }
    )


@router.get("/me/projects", summary="当前用户被授权的项目（供创建任务时选择）")
def my_projects(session: DbSession, user: CurrentUser) -> dict:
    """返回当前用户可用的项目清单（含名称）。

    为什么需要它
    ------------
    创建评估任务时，非管理员必须指定 ``project_id`` 且需在自己被授权的范围内
    （防止越权写入他人项目）。但此前只有 ``/admin/projects`` 能列出项目，
    而它是管理员专属 —— 于是**工程师有 eval:write 权限却无从得知该传哪个 ID**，
    前端也没有项目下拉，主流程实际走不通。

    本接口面向所有登录用户，只返回**其被授权**的项目：

    - 管理员：全部启用中的项目（不受 project_ids 限制）；
    - 其他角色：``sys_user.project_ids`` 对应的项目。

    返回值刻意与 ``/admin/projects`` 保持同样的字段形状，
    前端两个场景可复用同一套渲染逻辑。
    """
    from app.core.authz import is_admin

    stmt = select(Project).order_by(Project.id)
    if not is_admin(user):
        allowed = sorted(project_ids_of(user))
        if not allowed:
            return ok({"items": [], "total": 0, "can_auto_select": False})
        stmt = stmt.where(Project.id.in_(allowed))

    rows = session.execute(stmt).scalars().all()
    items = [
        {
            "id": p.id,
            "code": p.code,
            "name": p.name,
            "specialty": p.specialty,
            "status": p.status,
        }
        for p in rows
    ]
    return ok(
        {
            "items": items,
            "total": len(items),
            # 只有一个项目时前端无需让用户选，也提示后端可自动采用
            "can_auto_select": len(items) == 1,
        }
    )
