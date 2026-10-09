# -*- coding: utf-8 -*-
"""登录身份选择接口测试。

核心是**安全边界**：身份选择只是登录便利，绝不能决定权限。
本文件锁住三件事：
1. 演示身份只在允许的环境返回（生产环境返回空）；
2. 返回的身份与种子数据账号一致，且落点是各角色该去的页面；
3. 拿演示身份登录后，权限仍由服务端账号决定（客户端无法提权）。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


def test_demo_identities_disabled_in_production(client, monkeypatch):
    """生产环境不返回任何演示身份 —— 避免把可用凭据暴露出去。"""
    monkeypatch.setattr("app.config.settings.environment", "production", raising=False)
    monkeypatch.setattr("app.config.settings.seed_default_admin", True, raising=False)
    response = client.get("/api/v1/auth/demo-identities")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["enabled"] is False
    assert data["identities"] == []


def test_demo_identities_disabled_when_seed_off(client, monkeypatch):
    """关闭默认管理员种子时也不返回（那些账号根本不存在）。"""
    monkeypatch.setattr("app.config.settings.environment", "dev", raising=False)
    monkeypatch.setattr("app.config.settings.seed_default_admin", False, raising=False)
    response = client.get("/api/v1/auth/demo-identities")
    assert response.json()["data"]["enabled"] is False


def test_demo_identities_disabled_by_env_override(client, monkeypatch):
    """支持用环境变量强制关闭，无需改动数据库或种子配置。"""
    monkeypatch.setattr("app.config.settings.environment", "dev", raising=False)
    monkeypatch.setattr("app.config.settings.seed_default_admin", True, raising=False)
    monkeypatch.setenv("SUPERVISION_DEMO_LOGIN", "false")
    response = client.get("/api/v1/auth/demo-identities")
    assert response.json()["data"]["enabled"] is False


def test_demo_identities_in_dev(client, monkeypatch):
    monkeypatch.delenv("SUPERVISION_DEMO_LOGIN", raising=False)
    monkeypatch.setattr("app.config.settings.environment", "dev", raising=False)
    monkeypatch.setattr("app.config.settings.seed_default_admin", True, raising=False)
    monkeypatch.setattr("app.config.settings.seed_default_admin_password", "Test@12345", raising=False)

    response = client.get("/api/v1/auth/demo-identities")
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["enabled"] is True

    roles = {item["role"] for item in data["identities"]}
    assert roles == {"admin", "engineer", "expert", "kb_manager", "viewer"}

    # 每个身份都应给出可用的用户名与落点
    for item in data["identities"]:
        assert item["username"], f"{item['role']} 缺少用户名"
        assert item["password"] == "Test@12345"
        assert item["home"], f"{item['role']} 缺少落地页"
        assert item["label"] and item["description"]


def test_identity_home_matches_backend_role_capability(client, monkeypatch):
    """落点必须与该角色的实际权限相符，否则登录后会看到无权页面。

    这是**防跳转死循环**的关键校验：若落点页面该角色无权访问，
    路由守卫会把它踢回 home，home 又跳回该落点 —— 页面反复跳转、无法使用。

    注：`knowledge` 落点现在只要求 ``kb:read``（页面同时服务管理态与只读态，
    写操作由页面内 canWriteKb 控制），因此不再要求 kb:write。
    """
    monkeypatch.delenv("SUPERVISION_DEMO_LOGIN", raising=False)
    monkeypatch.setattr("app.config.settings.environment", "dev", raising=False)
    monkeypatch.setattr("app.config.settings.seed_default_admin", True, raising=False)

    from app.api.deps import ROLE_PERMISSIONS

    response = client.get("/api/v1/auth/demo-identities")
    identities = response.json()["data"]["identities"]

    # 每个落点所需的权限点（与前端路由 meta.perm / permAny 对应）
    home_requires = {
        "users": ["admin:*"],
        "system": ["admin:*"],
        "dashboard": ["admin:*"],
        "knowledge": ["kb:read"],
        "graph": ["kg:write", "eval:write"],
        "chat": ["retrieval:read"],
        "evaluation": ["eval:write"],
        "judge": ["eval:review", "judge:write"],
    }

    for item in identities:
        role, home = item["role"], item["home"]
        perms = ROLE_PERMISSIONS.get(role, set())
        required = home_requires.get(home)
        assert required, f"{role} 的落点 {home} 未在 home_requires 中登记，无法校验可达性"

        allowed = "*" in perms or any(p in perms for p in required)
        assert allowed, (
            f"{role} 的落点 {home} 需要 {required}，但该角色权限为 {sorted(perms)} —— "
            "登录后会被守卫踢回，形成反复跳转"
        )


def test_demo_login_does_not_grant_extra_privilege(client, requires_db, monkeypatch):
    """用演示身份登录后，权限以服务端账号为准 —— 客户端无法借此提权。

    这里以 viewer 为例：登录成功，但访问管理员接口仍应被拒。
    """
    monkeypatch.delenv("SUPERVISION_DEMO_LOGIN", raising=False)
    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_scope

    username = "viewer"
    with session_scope() as session:
        user = session.query(User).filter(User.username == username).one_or_none()
        if user is None:
            session.add(
                User(
                    username=username,
                    password_hash=hash_password("Admin@12345"),
                    role="viewer",
                    project_ids=[],
                )
            )

    login = client.post(
        "/api/v1/auth/login", json={"username": username, "password": "Admin@12345"}
    )
    assert login.status_code == 200, login.text
    body = login.json()["data"]
    # 角色来自服务端，不是请求参数
    assert body["user"]["role"] == "viewer"

    headers = {"Authorization": f"Bearer {body['access_token']}"}
    # 只读用户访问管理接口必须被拒
    assert client.get("/api/v1/admin/users", headers=headers).status_code == 403
    assert client.get("/api/v1/admin/projects", headers=headers).status_code == 403
