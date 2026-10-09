# -*- coding: utf-8 -*-
"""用户与项目管理接口测试（访问控制的运维闭环）。

覆盖：
1. 只有管理员能访问管理接口（越权返回 403）；
2. 创建用户时**必须**给非管理员分配项目，否则登录后看不到数据 —— 接口应提前拦住；
3. 更新项目授权后，该用户确实能看到新项目的数据（端到端闭环）；
4. ``GET /me/scope`` 对「未授权任何项目」的用户给出明确提示。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from app.core.security import create_access_token, hash_password
from app.db.models import Project, User


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def admin_headers(client, requires_db):
    """创建（或复用）一个管理员账号并返回鉴权头。"""
    from app.db.session import session_scope

    username = "um_admin"
    with session_scope() as session:
        row = session.query(User).filter(User.username == username).one_or_none()
        if row is None:
            row = User(
                username=username,
                full_name="用户管理测试管理员",
                password_hash=hash_password("Test@12345"),
                role="admin",
                project_ids=[],
            )
            session.add(row)
            session.flush()
        token = create_access_token(str(row.id), extra={"username": row.username})
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def projects(requires_db):
    """两个测试项目。"""
    from app.db.session import session_scope

    suffix = uuid.uuid4().hex[:8]
    codes = [f"UM-P1-{suffix}", f"UM-P2-{suffix}"]
    with session_scope() as session:
        rows = [Project(code=code, name=f"用户管理测试项目{idx}") for idx, code in enumerate(codes)]
        session.add_all(rows)
        session.flush()
        ids = [r.id for r in rows]

    yield ids

    with session_scope() as session:
        for pid in ids:
            obj = session.get(Project, pid)
            if obj is not None:
                session.delete(obj)


def _cleanup_user(username: str) -> None:
    from app.db.session import session_scope

    with session_scope() as session:
        row = session.query(User).filter(User.username == username).one_or_none()
        if row is not None:
            session.delete(row)


# --------------------------------------------------------------------------- #
# 权限
# --------------------------------------------------------------------------- #
def test_admin_endpoints_require_admin(client, requires_db, projects):
    """普通工程师无权访问用户管理接口。"""
    from app.db.session import session_scope

    username = f"um_engineer_{uuid.uuid4().hex[:8]}"
    with session_scope() as session:
        row = User(
            username=username,
            password_hash=hash_password("Test@12345"),
            role="engineer",
            project_ids=[projects[0]],
        )
        session.add(row)
        session.flush()
        token = create_access_token(str(row.id), extra={"username": row.username})

    try:
        headers = {"Authorization": f"Bearer {token}"}
        for method, path in (
            ("get", "/api/v1/admin/users"),
            ("get", "/api/v1/admin/projects"),
            ("post", "/api/v1/admin/users"),
        ):
            response = getattr(client, method)(path, headers=headers, json={}) if method == "post" else getattr(client, method)(path, headers=headers)
            assert response.status_code == 403, f"{path} 应返回 403，实际 {response.status_code}"
    finally:
        _cleanup_user(username)


# --------------------------------------------------------------------------- #
# 项目授权是必需项
# --------------------------------------------------------------------------- #
def test_create_non_admin_without_projects_is_rejected(client, admin_headers, projects):
    """非管理员没有项目授权 -> 登录后看不到任何项目数据，接口必须提前拦住。"""
    username = f"um_noproj_{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/api/v1/admin/users",
        headers=admin_headers,
        json={
            "username": username,
            "password": "Test@12345",
            "role": "engineer",
            "project_ids": [],
        },
    )
    assert response.status_code in (400, 422), f"应被拒绝，实际 {response.status_code}"
    _cleanup_user(username)


def test_create_user_with_projects_succeeds(client, admin_headers, projects):
    username = f"um_ok_{uuid.uuid4().hex[:8]}"
    try:
        response = client.post(
            "/api/v1/admin/users",
            headers=admin_headers,
            json={
                "username": username,
                "password": "Test@12345",
                "full_name": "带项目的新用户",
                "role": "engineer",
                "project_ids": [projects[0]],
            },
        )
        assert response.status_code == 200, response.text
        data = response.json()["data"]
        assert data["role"] == "engineer"
        assert data["project_ids"] == [projects[0]]
    finally:
        _cleanup_user(username)


def test_create_admin_without_projects_is_allowed(client, admin_headers):
    """管理员不需要项目授权（不受项目隔离限制）。"""
    username = f"um_admin2_{uuid.uuid4().hex[:8]}"
    try:
        response = client.post(
            "/api/v1/admin/users",
            headers=admin_headers,
            json={
                "username": username,
                "password": "Test@12345",
                "role": "admin",
                "project_ids": [],
            },
        )
        assert response.status_code == 200, response.text
    finally:
        _cleanup_user(username)


def test_create_user_with_unknown_project_is_rejected(client, admin_headers):
    username = f"um_badproj_{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/api/v1/admin/users",
        headers=admin_headers,
        json={
            "username": username,
            "password": "Test@12345",
            "role": "engineer",
            "project_ids": [99999999],
        },
    )
    assert response.status_code in (400, 422)
    _cleanup_user(username)


def test_invalid_role_is_rejected(client, admin_headers, projects):
    username = f"um_badrole_{uuid.uuid4().hex[:8]}"
    response = client.post(
        "/api/v1/admin/users",
        headers=admin_headers,
        json={
            "username": username,
            "password": "Test@12345",
            "role": "superuser",
            "project_ids": [projects[0]],
        },
    )
    assert response.status_code in (400, 422)
    _cleanup_user(username)


# --------------------------------------------------------------------------- #
# 授权变更闭环
# --------------------------------------------------------------------------- #
def test_update_projects_then_user_sees_them(client, admin_headers, requires_db, projects):
    """更新项目授权后，/me/scope 应立即反映（授权闭环）。"""
    from app.db.session import session_scope

    username = f"um_upd_{uuid.uuid4().hex[:8]}"
    try:
        created = client.post(
            "/api/v1/admin/users",
            headers=admin_headers,
            json={
                "username": username,
                "password": "Test@12345",
                "role": "engineer",
                "project_ids": [projects[0]],
            },
        )
        assert created.status_code == 200, created.text
        user_id = created.json()["data"]["id"]

        # 改为两个项目
        updated = client.post(
            f"/api/v1/admin/users/{user_id}/projects",
            headers=admin_headers,
            json={"project_ids": [projects[0], projects[1]]},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["data"]["project_ids"] == sorted([projects[0], projects[1]])

        with session_scope() as session:
            row = session.get(User, user_id)
            token = create_access_token(str(row.id), extra={"username": row.username})

        scope = client.get("/api/v1/me/scope", headers={"Authorization": f"Bearer {token}"})
        assert scope.status_code == 200
        body = scope.json()["data"]
        assert body["project_ids"] == sorted([projects[0], projects[1]])
        assert body["warning"] is None

        # 清空授权应被拒绝（那会让该用户看不到任何项目数据）
        cleared = client.post(
            f"/api/v1/admin/users/{user_id}/projects",
            headers=admin_headers,
            json={"project_ids": []},
        )
        assert cleared.status_code in (400, 422)
    finally:
        _cleanup_user(username)


def test_me_scope_warns_when_no_projects(client, requires_db):
    """未授权项目的用户应拿到明确提示，而不是困惑于「看不到数据」。"""
    from app.db.session import session_scope

    username = f"um_scope_{uuid.uuid4().hex[:8]}"
    with session_scope() as session:
        row = User(
            username=username,
            password_hash=hash_password("Test@12345"),
            role="engineer",
            project_ids=[],
        )
        session.add(row)
        session.flush()
        token = create_access_token(str(row.id), extra={"username": row.username})

    try:
        response = client.get("/api/v1/me/scope", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        body = response.json()["data"]
        assert body["project_ids"] == []
        assert body["warning"], "缺少项目授权时应给出提示"
        assert body["is_admin"] is False
    finally:
        _cleanup_user(username)


def test_cannot_deactivate_self(client, admin_headers):
    """管理员不能停用自己，避免把自己锁在门外。"""
    me = client.get("/api/v1/admin/users?keyword=um_admin", headers=admin_headers)
    assert me.status_code == 200
    items = me.json()["data"]["items"]
    assert items, "应能查到管理员自身"
    # 找到当前登录账号（用户名以 um_admin 开头且 role=admin）
    target = next(item for item in items if item["username"].startswith("um_admin"))
    response = client.post(
        f"/api/v1/admin/users/{target['id']}/status",
        headers=admin_headers,
        json={"is_active": False},
    )
    assert response.status_code in (400, 422), f"不应允许停用自己，实际 {response.status_code}"
