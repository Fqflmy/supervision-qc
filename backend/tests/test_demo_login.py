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
    assert roles == {"admin", "engineer", "expert", "viewer"}

    # 每个身份都应给出可用的用户名与落点
    for item in data["identities"]:
        assert item["username"], f"{item['role']} 缺少用户名"
        assert item["password"] == "Test@12345"
        assert item["home"], f"{item['role']} 缺少落地页"
        assert item["label"] and item["description"]


def test_identity_home_matches_backend_role_capability(client, monkeypatch):
    """落点必须与该角色的实际权限相符，否则登录后会看到无权页面。

    - 只有 admin 能进 users（管理页）
    - 只有 expert/admin 能进 judge 做复核
    - engineer 进评估任务
    """
    monkeypatch.delenv("SUPERVISION_DEMO_LOGIN", raising=False)
    monkeypatch.setattr("app.config.settings.environment", "dev", raising=False)
    monkeypatch.setattr("app.config.settings.seed_default_admin", True, raising=False)

    from app.api.deps import ROLE_PERMISSIONS

    response = client.get("/api/v1/auth/demo-identities")
    for item in response.json()["data"]["identities"]:
        role, home = item["role"], item["home"]
        perms = ROLE_PERMISSIONS.get(role, set())

        if home == "users":
            assert "*" in perms or "admin:*" in perms, f"{role} 落点 users 但无管理权限"
        if home == "judge" and role != "viewer":
            # 评审页需要能看到复核队列
            assert "eval:review" in perms or "*" in perms or "judge:write" in perms
        if home == "knowledge":
            assert "kb:write" in perms or "*" in perms, f"{role} 落点知识库但无写权限"
        if home == "evaluation":
            assert "eval:write" in perms or "*" in perms, f"{role} 落点评估但无发起权限"


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
