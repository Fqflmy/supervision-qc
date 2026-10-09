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
    full_name: Optional[str] = Field(default=None, max_length=64)
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


def _user_out(user: User) -> dict:
    return {
        "id": user.id,
        "username": user.username,
        "full_name": user.full_name,
        "email": user.email,
        "phone": user.phone,
        "role": user.role,
        "project_ids": sorted(project_ids_of(user)),
        "specialties": list(user.specialties or []),
        "is_active": user.is_active,
        "last_login_at": user.last_login_at,
        "created_at": user.created_at,
    }


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

    created = User(
        username=body.username.strip(),
        full_name=body.full_name,
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
