# -*- coding: utf-8 -*-
"""认证接口（FR-SYS-02）。"""
from __future__ import annotations

import datetime as dt
from typing import Annotated, Optional

from fastapi import APIRouter, Body, Request
from sqlalchemy import select

from app.api.deps import CurrentUser, DbSession, TraceId, client_ip, require_permission
from app.config import settings
from app.constants import AuditAction
from app.core.authz import ADMIN_ROLES
from app.core.errors import ForbiddenError, ParamInvalidError, UnauthenticatedError
from app.core.response import ok
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.core.logging_conf import get_logger
from app.db import add_audit_log, utcnow
from app.db.models import Project, User
from app.schemas.api import (
    LoginRequest,
    PasswordChangeRequest,
    RefreshRequest,
    TokenResponse,
    UserCreate,
    UserOut,
    UserUpdate,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/auth", tags=["认证"])


@router.post("/login", summary="登录并签发 Token")
def login(request: Request, session: DbSession, trace_id: TraceId, body: LoginRequest = Body(...)) -> dict:
    user = session.execute(select(User).where(User.username == body.username)).scalars().first()
    ip = client_ip(request)

    if user is not None and user.locked_until and user.locked_until > utcnow():
        add_audit_log(
            session, AuditAction.LOGIN.value, username=body.username, result="locked", ip=ip,
            detail={"locked_until": user.locked_until.isoformat()},
        )
        session.commit()
        raise ForbiddenError(f"账号已锁定，请于 {user.locked_until:%Y-%m-%d %H:%M} 后重试")

    if user is None or not verify_password(body.password, user.password_hash):
        if user is not None:
            user.failed_login_count += 1
            if user.failed_login_count >= settings.login_max_failures:
                user.locked_until = utcnow() + dt.timedelta(minutes=settings.login_lock_minutes)
                user.failed_login_count = 0
        add_audit_log(session, AuditAction.LOGIN.value, username=body.username, result="failed", ip=ip)
        session.commit()
        raise UnauthenticatedError("用户名或密码错误")

    if not user.is_active:
        raise ForbiddenError("账号已禁用，请联系管理员")

    user.failed_login_count = 0
    user.locked_until = None
    user.last_login_at = utcnow()
    add_audit_log(
        session, AuditAction.LOGIN.value,
        user_id=user.id, username=user.username, result="success", ip=ip,
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()

    access = create_access_token(
        user.id, roles=[user.role], extra={"username": user.username, "trace_id": trace_id}
    )
    refresh = create_refresh_token(user.id)
    payload = TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_expire_minutes * 60,
        user=UserOut.model_validate(user),
    )
    return ok(payload.model_dump())


@router.post("/refresh", summary="刷新 access token")
def refresh(session: DbSession, body: RefreshRequest = Body(...)) -> dict:
    payload = decode_token(body.refresh_token, expected_type="refresh")
    user = session.get(User, int(payload["sub"]))
    if user is None or not user.is_active:
        raise UnauthenticatedError("用户不可用")
    access = create_access_token(user.id, roles=[user.role], extra={"username": user.username})
    return ok(
        {
            "access_token": access,
            "token_type": "bearer",
            "expires_in": settings.access_token_expire_minutes * 60,
        }
    )


@router.get("/demo-identities", summary="演示身份列表（登录页快速选择）")
def demo_identities() -> dict:
    """返回可用于快速登录的演示账号。

    ⚠️ **安全说明（改动前务必理解）**

    本接口只服务于「演示/开发环境的登录便利」：选择身份仅影响
    *填入哪套凭据* 与 *登录后默认跳转哪个页面*，
    **绝不决定权限** —— 权限始终由服务端账号（``sys_user.role`` +
    ``sys_user.project_ids``）决定，客户端无法通过任何参数提升角色。
    登录成功后仍以账号在库中的真实角色为准，前端菜单与路由拦截也据此渲染。

    为避免把可用凭据暴露到生产环境：仅当 ``seed_default_admin=true``
    （即种子数据确实会创建这些账号）时才返回；生产环境应设为 false，
    本接口随即返回空列表，登录页不再显示身份选择。
    """
    import os

    # 额外兜底：显式配置为生产环境时一律不返回，避免漏改 seed_default_admin
    if (settings.environment or "dev").strip().lower() == "production":
        return ok({"enabled": False, "identities": []})
    if not settings.seed_default_admin:
        return ok({"enabled": False, "identities": []})
    # 允许用环境变量强制关闭（无需改动数据库种子配置）
    if os.environ.get("SUPERVISION_DEMO_LOGIN", "").strip().lower() in {"false", "0", "no"}:
        return ok({"enabled": False, "identities": []})

    password = settings.seed_default_admin_password
    # ⚠️ `home` 必须与前端 ROLE_HOME 一致，且指向该角色有权访问的页面。
    # viewer 原为 'dashboard'，但运行总览已收归管理员 —— 不同步会导致
    # 登录后立刻被路由守卫踢回，形成反复跳转。
    identities = [
        {
            "role": "engineer",
            "label": "监理工程师",
            "description": "发起质量评估、查条款引用链",
            "home": "evaluation",
            "username": "engineer",
            "password": password,
        },
        {
            "role": "expert",
            "label": "审核人员",
            "description": "人工复核、确认评估结论",
            "home": "judge",
            "username": "expert",
            "password": password,
        },
        {
            "role": "kb_manager",
            "label": "知识库管理员",
            "description": "规范入库、解析、构建知识图谱",
            "home": "knowledge",
            "username": "kb_manager",
            "password": password,
        },
        {
            "role": "viewer",
            "label": "普通用户",
            "description": "只读查看授权项目的评估报告与规范原文",
            "home": "reports",
            "username": "viewer",
            "password": password,
        },
        {
            "role": "admin",
            "label": "系统管理员",
            "description": "用户与项目授权、系统与审计",
            "home": "users",
            "username": "admin",
            "password": password,
        },
    ]
    return ok({"enabled": True, "identities": identities})


@router.post("/logout", summary="登出（写审计）")
def logout(request: Request, session: DbSession, user: CurrentUser) -> dict:
    add_audit_log(
        session, AuditAction.LOGOUT.value, user_id=user.id, username=user.username,
        ip=client_ip(request),
    )
    session.commit()
    return ok({"message": "已登出"})


@router.get("/me", summary="当前用户信息")
def me(user: CurrentUser) -> dict:
    return ok(UserOut.model_validate(user).model_dump())


@router.get("/profile", summary="个人中心：身份信息 + 访问范围")
def get_profile(session: DbSession, user: CurrentUser) -> dict:
    """个人中心数据（**任何已登录用户可访问自己的**）。

    与 ``/admin/users/{id}`` 的区别：后者是管理员视角，可看他人；
    本接口只能看**自己**，且不含管理字段（角色/项目授权由管理员维护）。

    为什么需要它：用户应能确认「系统里登记的我是谁」——
    报告签认会用到姓名/单位/岗位，信息错了要能发现并联系管理员更正。
    """
    from app.core.authz import project_ids_of

    projects = []
    ids = sorted(project_ids_of(user))
    if ids:
        rows = session.execute(
            select(Project).where(Project.id.in_(ids)).order_by(Project.id)
        ).scalars().all()
        projects = [{"id": p.id, "code": p.code, "name": p.name} for p in rows]

    return ok(
        {
            "user": UserOut.model_validate(user).model_dump(),
            "projects": projects,
            # 管理员不受项目隔离限制，前端据此显示「不受限」而非「未授权」
            "is_admin": str(user.role).lower() in ADMIN_ROLES,
        }
    )


@router.post("/password", summary="个人中心：自助修改密码")
def change_password(
    request: Request,
    session: DbSession,
    user: CurrentUser,
    trace_id: TraceId,
    body: PasswordChangeRequest = Body(...),
) -> dict:
    """用户自助修改密码（需验证当前密码）。

    为什么必须有它：**在此之前系统没有自助改密能力**，
    用户忘记或想更换密码只能找管理员重置 —— 这既增加了管理员负担，
    也让「要求下次登录后修改密码」这个标记形同虚设（用户无处可改）。

    ⚠️ 安全要点
    -----------
    1. **必须验证当前密码**：否则 token 泄漏即等于账号被永久接管
       （攻击者可静默改密把真实用户锁在外面）；
    2. 新密码不得与旧密码相同：否则「改密」毫无意义，
       也让强制改密流程可以被一步绕过；
    3. 审计日志**只记录「已修改」这一事实，绝不记录密码内容**；
    4. 改密成功后**重新签发 access token**（清除 must_change_password 标记），
       否则前端仍带着标记，会陷入「改完还被拦」的死循环。
    """
    if not verify_password(body.old_password, user.password_hash):
        add_audit_log(
            session,
            AuditAction.CONFIG_CHANGE.value,
            user_id=user.id,
            username=user.username,
            result="failed",
            ip=client_ip(request),
            object_type="sys_user",
            object_id=str(user.id),
            detail={"action": "change_password", "reason": "old_password_mismatch"},
        )
        session.commit()
        # 不透露「旧密码错误」以外的信息（避免账号枚举），但这里用户已认证，可以直接说
        raise ParamInvalidError("当前密码不正确")

    if body.new_password == body.old_password:
        raise ParamInvalidError("新密码不能与当前密码相同")

    # 强度校验与建号/管理员重置使用**同一份规则**（PROFILE 改动前这里漏了，
    # 会出现「建号时拦、改密时放行」的不一致 —— 用户可先弱密码改密再绕过策略）。
    validate_password_strength(body.new_password)

    user.password_hash = hash_password(body.new_password)
    # 改密完成即清除强制改密标记
    user.must_change_password = False
    session.flush()

    add_audit_log(
        session,
        AuditAction.CONFIG_CHANGE.value,
        user_id=user.id,
        username=user.username,
        ip=client_ip(request),
        object_type="sys_user",
        object_id=str(user.id),
        # 只记录事实，不记录密码
        detail={"action": "change_password", "self_service": True},
    )
    session.commit()

    # 重新签发 token：让前端立刻拿到 must_change_password=False，
    # 否则用户改完密码仍被守卫拦在个人中心。
    access = create_access_token(
        user.id, roles=[user.role], extra={"username": user.username, "trace_id": trace_id}
    )
    refresh = create_refresh_token(user.id)
    logger.info("用户自助修改密码成功", extra={"user": user.username})
    return ok(
        {
            "access_token": access,
            "refresh_token": refresh,
            "token_type": "bearer",
            "expires_in": settings.access_token_expire_minutes * 60,
            "user": UserOut.model_validate(user).model_dump(),
        }
    )


@router.get("/users", summary="用户列表（管理员）")
def list_users(session: DbSession, _: Annotated[User, require_permission("admin:*")]) -> dict:
    users = session.execute(select(User).order_by(User.id)).scalars().all()
    return ok([UserOut.model_validate(u).model_dump() for u in users])


@router.post("/users", summary="创建用户（管理员）")
def create_user(
    session: DbSession,
    _: Annotated[User, require_permission("admin:*")],
    body: UserCreate = Body(...),
) -> dict:
    exists = session.execute(select(User).where(User.username == body.username)).scalars().first()
    if exists is not None:
        from app.core.errors import ConflictError

        raise ConflictError(f"用户名已存在：{body.username}")
    validate_password_strength(body.password)
    user = User(
        username=body.username,
        password_hash=hash_password(body.password),
        full_name=body.full_name,
        email=body.email,
        role=body.role,
        specialties=body.specialties,
    )
    session.add(user)
    session.flush()
    add_audit_log(session, AuditAction.CONFIG_CHANGE.value, object_type="user", object_id=user.id)
    session.commit()
    return ok(UserOut.model_validate(user).model_dump())


@router.patch("/users/{user_id}", summary="更新用户（管理员）")
def update_user(
    user_id: int,
    session: DbSession,
    _: Annotated[User, require_permission("admin:*")],
    body: UserUpdate = Body(...),
) -> dict:
    from app.core.errors import NotFoundError

    user = session.get(User, user_id)
    if user is None:
        raise NotFoundError(f"用户不存在：{user_id}")
    data = body.model_dump(exclude_unset=True)
    password = data.pop("password", None)
    if password:
        validate_password_strength(password)
        user.password_hash = hash_password(password)
    for key, value in data.items():
        if value is not None:
            setattr(user, key, value)
    add_audit_log(session, AuditAction.CONFIG_CHANGE.value, object_type="user", object_id=user_id)
    session.commit()
    return ok(UserOut.model_validate(user).model_dump())
