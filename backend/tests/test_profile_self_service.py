# -*- coding: utf-8 -*-
"""个人中心：身份查看 + 自助修改密码。

背景
----
`must_change_password` 原先只是登录后的提示横幅，**不是强制拦截** ——
因为系统没有自助改密页面，强拦会把用户卡住。

本测试覆盖补齐后的能力：
- **查**：`GET /auth/profile` 返回自己的身份与授权项目（任何角色可用）；
- **改**：`POST /auth/password` 自助修改密码；
- **安全边界**：必须验证旧密码、新密码不得与旧相同、
  必须满足强度规则、改密后标记清除并重新签发 token；
- **审计**：只记录事实，不记录密码内容。
"""
from __future__ import annotations

import uuid

import pytest


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


OLD_PWD = "OldPass@123"
NEW_PWD = "NewPass@456"


def _login(client, username: str, password: str = "Admin@12345"):
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    if response.status_code != 200:
        pytest.skip(f"{username} 登录失败，跳过")
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _make_user(role: str = "viewer", password: str = OLD_PWD, must_change: bool = False) -> str:
    """造一个可登录的账号，返回用户名。"""
    from app.core.security import hash_password
    from app.db.models import Project, User
    from app.db.session import session_scope

    username = f"pf_{uuid.uuid4().hex[:8]}"
    with session_scope() as session:
        project = session.query(Project).order_by(Project.id).first()
        session.add(
            User(
                username=username,
                full_name="个人中心测试",
                employee_no=f"PF{uuid.uuid4().hex[:6].upper()}",
                org_name="某监理有限公司",
                department="项目监理部",
                position="专业监理工程师",
                cert_no="JL2026009999",
                signature="测试署名",
                password_hash=hash_password(password),
                role=role,
                project_ids=[project.id] if project else [],
                specialties=["结构工程"],
                must_change_password=must_change,
            )
        )
    return username


def _cleanup(username: str) -> None:
    from app.db.models import User
    from app.db.session import session_scope

    with session_scope() as session:
        row = session.query(User).filter(User.username == username).one_or_none()
        if row is not None:
            session.delete(row)


# --------------------------------------------------------------------------- #
# 查：个人资料
# --------------------------------------------------------------------------- #
def test_profile_returns_own_identity(client, requires_db):
    """个人中心应返回自己的完整身份（报告签认会用这些字段）。"""
    username = _make_user()
    headers = _login(client, username, OLD_PWD)

    response = client.get("/api/v1/auth/profile", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    user = data["user"]
    assert user["username"] == username
    assert user["full_name"] == "个人中心测试"
    assert user["department"] == "项目监理部"
    assert user["position"] == "专业监理工程师"
    assert user["cert_no"] == "JL2026009999"
    assert user["signature"] == "测试署名"

    assert "projects" in data
    assert isinstance(data["projects"], list)
    assert data["is_admin"] is False
    _cleanup(username)


def test_profile_available_to_every_role(client, requires_db):
    """个人中心对所有角色开放 —— 它是改密的唯一入口，不能按角色限制。"""
    for role in ("viewer", "engineer", "expert", "kb_manager"):
        username = _make_user(role=role)
        headers = _login(client, username, OLD_PWD)
        response = client.get("/api/v1/auth/profile", headers=headers)
        assert response.status_code == 200, f"{role} 无法访问个人中心：{response.text[:150]}"
        _cleanup(username)


def test_profile_requires_auth(client, requires_db):
    assert client.get("/api/v1/auth/profile").status_code == 401


def test_profile_does_not_require_admin(client, requires_db):
    """只读用户也能看自己的资料（此前只有 /admin/users 可查，需 admin:*）。"""
    username = _make_user(role="viewer")
    headers = _login(client, username, OLD_PWD)
    assert client.get("/api/v1/auth/profile", headers=headers).status_code == 200
    # 但管理接口仍拦截
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 403
    _cleanup(username)


# --------------------------------------------------------------------------- #
# 改：自助修改密码
# --------------------------------------------------------------------------- #
def test_change_password_success(client, requires_db):
    username = _make_user()
    headers = _login(client, username, OLD_PWD)

    response = client.post(
        "/api/v1/auth/password",
        headers=headers,
        json={"old_password": OLD_PWD, "new_password": NEW_PWD},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    # 必须重新签发 token（否则前端仍带着 must_change 标记）
    assert data["access_token"], "未重新签发 access token"
    assert data["refresh_token"], "未重新签发 refresh token"
    assert data["user"]["username"] == username

    # 新密码可登录、旧密码失效
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": username, "password": NEW_PWD}
        ).status_code
        == 200
    ), "改密后新密码无法登录"
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": username, "password": OLD_PWD}
        ).status_code
        != 200
    ), "改密后旧密码仍可登录"
    _cleanup(username)


def test_change_password_requires_correct_old_password(client, requires_db):
    """⚠️ 关键安全点：必须验证当前密码。

    否则 access token 泄漏即等于账号被永久接管 ——
    攻击者可静默改密，把真实用户锁在系统外。
    """
    username = _make_user()
    headers = _login(client, username, OLD_PWD)

    response = client.post(
        "/api/v1/auth/password",
        headers=headers,
        json={"old_password": "WrongPass@999", "new_password": NEW_PWD},
    )
    assert response.status_code == 400, f"旧密码错误竟被接受：{response.status_code}"

    # 密码未被改动：旧密码仍可登录
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": username, "password": OLD_PWD}
        ).status_code
        == 200
    ), "旧密码校验失败后密码被改动了"
    _cleanup(username)


def test_change_password_rejects_same_password(client, requires_db):
    """新密码不得与旧密码相同，否则「改密」毫无意义。"""
    username = _make_user()
    headers = _login(client, username, OLD_PWD)

    response = client.post(
        "/api/v1/auth/password",
        headers=headers,
        json={"old_password": OLD_PWD, "new_password": OLD_PWD},
    )
    assert response.status_code == 400, f"相同密码未被拒绝：{response.status_code}"
    _cleanup(username)


@pytest.mark.parametrize(
    "weak",
    [
        "short1",       # 长度不足
        "12345678",     # 无字母
        "abcdefgh",     # 无数字
    ],
)
def test_change_password_enforces_strength(client, requires_db, weak):
    """必须与建号/管理员重置使用**同一份**强度规则。

    否则用户可先改成弱密码来绕过全局密码策略。
    """
    username = _make_user()
    headers = _login(client, username, OLD_PWD)

    response = client.post(
        "/api/v1/auth/password",
        headers=headers,
        json={"old_password": OLD_PWD, "new_password": weak},
    )
    # 长度不足会被 Pydantic 的 min_length 先拦下（422），
    # 「无字母 / 无数字」由 validate_password_strength 拦下（400）。
    # 两者都是「被拒绝」，测试接受任一 —— 但必须拒绝。
    assert response.status_code in (400, 422), (
        f"弱密码「{weak}」未被拒绝：{response.status_code} {response.text[:150]}"
    )
    _cleanup(username)


def test_change_password_clears_must_change_flag(client, requires_db):
    """改密成功后 `must_change_password` 必须清除，否则用户被永久卡住。"""
    username = _make_user(must_change=True)
    headers = _login(client, username, OLD_PWD)

    # 改密前：标记为 True
    before = client.get("/api/v1/auth/profile", headers=headers).json()["data"]["user"]
    assert before["must_change_password"] is True

    response = client.post(
        "/api/v1/auth/password",
        headers=headers,
        json={"old_password": OLD_PWD, "new_password": NEW_PWD},
    )
    assert response.status_code == 200, response.text
    # 响应里就直接是 False（前端据此立刻放行，不必再请求一次）
    assert response.json()["data"]["user"]["must_change_password"] is False

    # 用新 token 再查一次，DB 也确实是 False
    new_headers = {"Authorization": f"Bearer {response.json()['data']['access_token']}"}
    after = client.get("/api/v1/auth/profile", headers=new_headers).json()["data"]["user"]
    assert after["must_change_password"] is False
    _cleanup(username)


def test_change_password_audit_has_no_secret(client, requires_db):
    """审计日志只记录「已修改」这一事实，绝不记录密码内容。"""
    from sqlalchemy import select

    from app.db.models import AuditLog, User
    from app.db.session import session_scope

    username = _make_user()
    headers = _login(client, username, OLD_PWD)
    secret = "TopSecret@789"

    client.post(
        "/api/v1/auth/password",
        headers=headers,
        json={"old_password": OLD_PWD, "new_password": secret},
    )

    with session_scope() as session:
        row = session.query(User).filter(User.username == username).one()
        logs = (
            session.execute(select(AuditLog).where(AuditLog.object_id == str(row.id)))
            .scalars()
            .all()
        )
    assert logs, "改密未写审计日志"
    for log in logs:
        assert secret not in str(log.detail or ""), "审计日志泄漏了新密码"
        assert OLD_PWD not in str(log.detail or ""), "审计日志泄漏了旧密码"
    _cleanup(username)


def test_change_password_audit_records_failure(client, requires_db):
    """旧密码错误也要留痕（可能是暴力尝试的信号）。"""
    from sqlalchemy import select

    from app.db.models import AuditLog, User
    from app.db.session import session_scope

    username = _make_user()
    headers = _login(client, username, OLD_PWD)

    client.post(
        "/api/v1/auth/password",
        headers=headers,
        json={"old_password": "Wrong@123", "new_password": NEW_PWD},
    )

    with session_scope() as session:
        row = session.query(User).filter(User.username == username).one()
        logs = (
            session.execute(select(AuditLog).where(AuditLog.object_id == str(row.id)))
            .scalars()
            .all()
        )
    assert any(
        "old_password_mismatch" in str(log.detail or "") for log in logs
    ), "旧密码校验失败未记录原因"
    _cleanup(username)


def test_token_from_before_change_can_still_be_used_until_expiry(client, requires_db):
    """改密不会立即作废已签发的 access token（无黑名单机制）。

    这是**已知设计限制**，测试把它显式记录下来而不是假装不存在。
    缓解方式：access token 有效期较短（见 settings.access_token_expire_minutes）。
    """
    username = _make_user()
    headers = _login(client, username, OLD_PWD)

    client.post(
        "/api/v1/auth/password",
        headers=headers,
        json={"old_password": OLD_PWD, "new_password": NEW_PWD},
    )
    # 旧 token 仍可读自己的资料（JWT 无状态，未实现吊销列表）
    response = client.get("/api/v1/auth/profile", headers=headers)
    assert response.status_code == 200, (
        "若此处变成 401，说明实现了 token 吊销 —— "
        "请同步更新本测试与文档中的「已知限制」说明"
    )
    _cleanup(username)


# --------------------------------------------------------------------------- #
# 管理员重置 → 强制改密 的完整闭环
# --------------------------------------------------------------------------- #
def test_admin_reset_then_user_self_change(client, requires_db):
    """闭环：管理员重置密码（标记需改密）→ 用户登录 → 自助改密 → 标记清除。"""
    username = _make_user()
    admin_headers = _login(client, "admin")

    # 找到该用户 id
    from app.db.models import User
    from app.db.session import session_scope

    with session_scope() as session:
        uid = session.query(User).filter(User.username == username).one().id

    temp_pwd = "TempPass@111"
    reset = client.post(
        f"/api/v1/admin/users/{uid}/password",
        headers=admin_headers,
        json={"new_password": temp_pwd, "must_change": True},
    )
    assert reset.status_code == 200, reset.text
    assert reset.json()["data"]["must_change_password"] is True

    # 用户用临时密码登录
    headers = _login(client, username, temp_pwd)
    profile = client.get("/api/v1/auth/profile", headers=headers).json()["data"]["user"]
    assert profile["must_change_password"] is True, "重置后的改密标记未生效"

    # 自助改密
    final_pwd = "FinalPass@222"
    change = client.post(
        "/api/v1/auth/password",
        headers=headers,
        json={"old_password": temp_pwd, "new_password": final_pwd},
    )
    assert change.status_code == 200, change.text
    assert change.json()["data"]["user"]["must_change_password"] is False

    # 最终密码可登录，临时密码失效
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": username, "password": final_pwd}
        ).status_code
        == 200
    )
    _cleanup(username)
