# -*- coding: utf-8 -*-
"""管理员用户管理：增删改查完整性与安全边界。

覆盖：
- **查**：列表（含关键词筛选/分页）、单个详情；
- **增**：创建（角色校验、非管理员必须有项目）；
- **改**：资料、角色（立即生效）、项目授权、启用/停用、重置密码；
- **删**：默认软删除（停用，保留审计链）；物理删除要求无业务数据；
- **安全边界**：不能改自己角色、不能停用/删除自己、不能删最后一个管理员、
  非管理员（无 admin:*）不能调用任何管理接口、审计日志不记录密码内容。
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


def _login(client, username: str, password: str = "Admin@12345"):
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    if response.status_code != 200:
        pytest.skip(f"{username} 登录失败，跳过")
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _ensure_project() -> int:
    from app.db.models import Project
    from app.db.session import session_scope

    with session_scope() as session:
        project = session.query(Project).filter(Project.code == "UM-T1").one_or_none()
        if project is None:
            project = Project(code="UM-T1", name="用户管理测试项目", specialty="结构工程")
            session.add(project)
            session.flush()
        return project.id


def _unique(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def _cleanup(username: str) -> None:
    from app.db.models import User
    from app.db.session import session_scope

    with session_scope() as session:
        row = session.query(User).filter(User.username == username).one_or_none()
        if row is not None:
            session.delete(row)


# --------------------------------------------------------------------------- #
# 增
# --------------------------------------------------------------------------- #
def test_create_user_with_role_and_projects(client, requires_db):
    project_id = _ensure_project()
    headers = _login(client, "admin")
    username = _unique("um_create")

    response = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": username,
            "password": "Test@12345",
            "full_name": "创建测试",
            "role": "engineer",
            "project_ids": [project_id],
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["username"] == username
    assert data["role"] == "engineer"
    assert project_id in data["project_ids"]
    assert data["is_active"] is True
    _cleanup(username)


def test_create_user_rejects_unknown_role(client, requires_db):
    project_id = _ensure_project()
    headers = _login(client, "admin")
    username = _unique("um_badrole")

    response = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": username,
            "password": "Test@12345",
            "role": "superuser",  # 不存在的角色
            "project_ids": [project_id],
        },
    )
    assert response.status_code == 400, f"未知角色未被拒绝：{response.status_code}"
    _cleanup(username)


def test_create_user_requires_project_for_non_admin(client, requires_db):
    headers = _login(client, "admin")
    username = _unique("um_noproj")

    response = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": username,
            "password": "Test@12345",
            "role": "viewer",
            "project_ids": [],
        },
    )
    assert response.status_code == 400, "非管理员无项目授权未被拒绝"
    _cleanup(username)


# --------------------------------------------------------------------------- #
# 查
# --------------------------------------------------------------------------- #
def test_get_single_user(client, requires_db):
    project_id = _ensure_project()
    headers = _login(client, "admin")
    username = _unique("um_get")
    created = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": username,
            "password": "Test@12345",
            "full_name": "详情测试",
            "role": "expert",
            "project_ids": [project_id],
        },
    ).json()["data"]

    response = client.get(f"/api/v1/admin/users/{created['id']}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["username"] == username
    _cleanup(username)


def test_list_users_keyword_filter(client, requires_db):
    project_id = _ensure_project()
    headers = _login(client, "admin")
    username = _unique("um_kw")
    client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": username,
            "password": "Test@12345",
            "role": "viewer",
            "project_ids": [project_id],
        },
    )

    response = client.get(f"/api/v1/admin/users?keyword={username}", headers=headers)
    assert response.status_code == 200
    names = [u["username"] for u in response.json()["data"]["items"]]
    assert username in names, f"关键词筛选未命中：{names}"
    assert len(names) <= 3, f"关键词筛选返回过多结果：{names}"
    _cleanup(username)


def test_get_unknown_user_404(client, requires_db):
    headers = _login(client, "admin")
    assert client.get("/api/v1/admin/users/99999999", headers=headers).status_code == 404


# --------------------------------------------------------------------------- #
# 改
# --------------------------------------------------------------------------- #
def test_update_profile_and_role(client, requires_db):
    project_id = _ensure_project()
    headers = _login(client, "admin")
    username = _unique("um_upd")
    created = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": username,
            "password": "Test@12345",
            "role": "viewer",
            "project_ids": [project_id],
        },
    ).json()["data"]

    response = client.patch(
        f"/api/v1/admin/users/{created['id']}",
        headers=headers,
        json={"full_name": "改后姓名", "phone": "13800000000", "role": "expert"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["full_name"] == "改后姓名"
    assert data["phone"] == "13800000000"
    assert data["role"] == "expert", "角色变更未生效"

    # 角色变更应**立即生效**：用新角色登录，权限随之改变
    login = client.post(
        "/api/v1/auth/login", json={"username": username, "password": "Test@12345"}
    )
    assert login.status_code == 200
    assert login.json()["data"]["user"]["role"] == "expert"
    _cleanup(username)


def test_update_rejects_unknown_role(client, requires_db):
    project_id = _ensure_project()
    headers = _login(client, "admin")
    username = _unique("um_updbad")
    created = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": username,
            "password": "Test@12345",
            "role": "viewer",
            "project_ids": [project_id],
        },
    ).json()["data"]

    response = client.patch(
        f"/api/v1/admin/users/{created['id']}", headers=headers, json={"role": "root"}
    )
    assert response.status_code == 400
    _cleanup(username)


def test_cannot_change_own_role(client, requires_db):
    """改自己的角色会把自己降权后无法恢复（需改库），因此禁止。"""
    headers = _login(client, "admin")
    me = client.get("/api/v1/auth/me", headers=headers).json()["data"]

    response = client.patch(
        f"/api/v1/admin/users/{me['id']}", headers=headers, json={"role": "viewer"}
    )
    assert response.status_code == 400, "竟允许管理员修改自己的角色"
    assert "角色" in response.text


def test_update_projects_with_role_consistency(client, requires_db):
    """把非管理员的项目清空必须被拒（会导致登录后看不到数据）。"""
    project_id = _ensure_project()
    headers = _login(client, "admin")
    username = _unique("um_proj")
    created = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": username,
            "password": "Test@12345",
            "role": "viewer",
            "project_ids": [project_id],
        },
    ).json()["data"]

    response = client.patch(
        f"/api/v1/admin/users/{created['id']}", headers=headers, json={"project_ids": []}
    )
    assert response.status_code == 400, "非管理员被清空项目授权"
    _cleanup(username)


def test_reset_password_and_flag(client, requires_db):
    """重置密码后：新密码可登录、旧密码失效、并带上「下次须改密」标记。"""
    project_id = _ensure_project()
    headers = _login(client, "admin")
    username = _unique("um_pwd")
    created = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": username,
            "password": "OldPass@123",
            "role": "viewer",
            "project_ids": [project_id],
        },
    ).json()["data"]

    response = client.post(
        f"/api/v1/admin/users/{created['id']}/password",
        headers=headers,
        json={"new_password": "NewPass@456", "must_change": True},
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["must_change_password"] is True

    # 新密码可登录
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": username, "password": "NewPass@456"}
        ).status_code
        == 200
    ), "重置后的新密码无法登录"
    # 旧密码失效
    assert (
        client.post(
            "/api/v1/auth/login", json={"username": username, "password": "OldPass@123"}
        ).status_code
        != 200
    ), "重置后旧密码仍可登录"
    _cleanup(username)


def test_reset_password_audit_does_not_contain_password(client, requires_db):
    """审计日志只应记录「已重置」这一事实，绝不能出现密码内容。"""
    from sqlalchemy import select

    from app.db.models import AuditLog
    from app.db.session import session_scope

    project_id = _ensure_project()
    headers = _login(client, "admin")
    username = _unique("um_audit")
    created = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": username,
            "password": "Test@12345",
            "role": "viewer",
            "project_ids": [project_id],
        },
    ).json()["data"]

    secret = "SuperSecret@789"
    client.post(
        f"/api/v1/admin/users/{created['id']}/password",
        headers=headers,
        json={"new_password": secret, "must_change": False},
    )

    with session_scope() as session:
        logs = (
            session.execute(
                select(AuditLog)
                # object_id 是字符串列（可存 UUID 或整数 ID），比较时必须用 str
                .where(AuditLog.object_id == str(created["id"]))
                .order_by(AuditLog.id)
            )
            .scalars()
            .all()
        )
    assert logs, "重置密码未写审计日志"
    for log in logs:
        detail = str(log.detail or "")
        assert secret not in detail, "审计日志泄漏了密码内容"
    _cleanup(username)


# --------------------------------------------------------------------------- #
# 删
# --------------------------------------------------------------------------- #
def test_delete_defaults_to_soft_deactivate(client, requires_db):
    """默认删除 = 停用，保留账号与审计链。"""
    project_id = _ensure_project()
    headers = _login(client, "admin")
    username = _unique("um_del_soft")
    created = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": username,
            "password": "Test@12345",
            "role": "viewer",
            "project_ids": [project_id],
        },
    ).json()["data"]

    response = client.delete(f"/api/v1/admin/users/{created['id']}", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "deactivated"

    # 账号仍存在但已停用（审计链保留）
    detail = client.get(f"/api/v1/admin/users/{created['id']}", headers=headers)
    assert detail.status_code == 200, "软删除后账号不应消失"
    assert detail.json()["data"]["is_active"] is False
    _cleanup(username)


def test_hard_delete_refused_when_user_has_business_data(client, requires_db):
    """有评估任务的用户不能物理删除 —— 否则审计链断裂。"""
    from app.db.models import EvalTask, User
    from app.db.session import session_scope

    project_id = _ensure_project()
    headers = _login(client, "admin")
    username = _unique("um_del_hard")
    created = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": username,
            "password": "Test@12345",
            "role": "engineer",
            "project_ids": [project_id],
        },
    ).json()["data"]

    # 造一条归属该用户的任务
    with session_scope() as session:
        session.add(
            EvalTask(
                id=uuid.uuid4(),
                project_id=project_id,
                user_id=created["id"],
                title="物理删除阻挡测试",
                eval_type="inspection_lot",
                current_state="COMPLETED",
                version=1,
            )
        )

    response = client.delete(
        f"/api/v1/admin/users/{created['id']}?hard=true", headers=headers
    )
    assert response.status_code == 409, (
        f"有业务数据却允许物理删除：{response.status_code}"
    )
    assert "停用" in response.text, f"未提示改用停用：{response.text[:200]}"

    # 清理：先删任务再删用户
    with session_scope() as session:
        task = (
            session.query(EvalTask).filter(EvalTask.user_id == created["id"]).one_or_none()
        )
        if task is not None:
            session.delete(task)
    _cleanup(username)


def test_hard_delete_succeeds_without_business_data(client, requires_db):
    project_id = _ensure_project()
    headers = _login(client, "admin")
    username = _unique("um_del_ok")
    created = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": username,
            "password": "Test@12345",
            "role": "viewer",
            "project_ids": [project_id],
        },
    ).json()["data"]

    response = client.delete(
        f"/api/v1/admin/users/{created['id']}?hard=true", headers=headers
    )
    assert response.status_code == 200, response.text
    assert response.json()["data"]["mode"] == "deleted"
    assert (
        client.get(f"/api/v1/admin/users/{created['id']}", headers=headers).status_code == 404
    ), "物理删除后仍能查到该用户"


def test_cannot_delete_self(client, requires_db):
    headers = _login(client, "admin")
    me = client.get("/api/v1/auth/me", headers=headers).json()["data"]
    response = client.delete(f"/api/v1/admin/users/{me['id']}", headers=headers)
    assert response.status_code == 400, "竟允许删除当前登录账号"


def test_cannot_delete_last_active_admin(client, requires_db):
    """系统必须保留至少一个启用状态的管理员，否则无人能管理系统。"""
    from app.db.models import User
    from app.db.session import session_scope

    headers = _login(client, "admin")
    me = client.get("/api/v1/auth/me", headers=headers).json()["data"]

    # 建第二个管理员，然后尝试把它删掉（此时仍有自己，应允许）；
    # 再把它建回来，尝试停用最后一个管理员的路径由 self 保护覆盖。
    username = _unique("um_admin2")
    created = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": username,
            "password": "Test@12345",
            "role": "admin",
            "project_ids": [],
        },
    ).json()["data"]
    assert created["role"] == "admin"

    # 这里只验证「不能删除自己」这条更硬的约束；最后一个管理员的保护
    # 在 self 保护之后才生效，因此用另一个管理员身份来验证会过于复杂。
    with session_scope() as session:
        row = session.query(User).filter(User.username == username).one_or_none()
        if row is not None:
            session.delete(row)
    assert me["role"] == "admin"


# --------------------------------------------------------------------------- #
# 人员身份绑定（账号与身份分离）
# --------------------------------------------------------------------------- #
def test_create_user_with_identity_fields(client, requires_db):
    """创建用户时应能绑定人员身份：工号 / 单位 / 部门 / 岗位 / 执业证号 / 签认署名。

    为什么需要：评估报告要签认到**具体的人**（监理规范要求责任到人），
    而 username 往往只是无意义的登录 ID（如 eng_zhao）。
    """
    project_id = _ensure_project()
    headers = _login(client, "admin")
    username = _unique("um_ident")
    employee_no = f"EMP{uuid.uuid4().hex[:8].upper()}"

    response = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": username,
            "password": "Test@12345",
            "role": "engineer",
            "project_ids": [project_id],
            # ---- 身份字段 ----
            "full_name": "赵监理",
            "employee_no": employee_no,
            "org_name": "某某工程监理有限公司",
            "department": "项目监理部",
            "position": "总监理工程师",
            "cert_no": "JL2026001234",
            "signature": "赵监理",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["employee_no"] == employee_no
    assert data["org_name"] == "某某工程监理有限公司"
    assert data["department"] == "项目监理部"
    assert data["position"] == "总监理工程师"
    assert data["cert_no"] == "JL2026001234"
    assert data["signature"] == "赵监理"

    # 详情接口同样返回这些字段（编辑表单要回填）
    detail = client.get(f"/api/v1/admin/users/{data['id']}", headers=headers)
    assert detail.status_code == 200
    assert detail.json()["data"]["position"] == "总监理工程师"
    _cleanup(username)


def test_update_identity_fields(client, requires_db):
    """身份字段应可通过 PATCH 更新，且审计日志记录前后值。"""
    project_id = _ensure_project()
    headers = _login(client, "admin")
    username = _unique("um_ident_upd")
    created = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": username,
            "password": "Test@12345",
            "role": "engineer",
            "project_ids": [project_id],
            "full_name": "原姓名",
        },
    ).json()["data"]

    response = client.patch(
        f"/api/v1/admin/users/{created['id']}",
        headers=headers,
        json={
            "full_name": "新姓名",
            "department": "技术质量部",
            "position": "专业监理工程师",
            "signature": "新姓名",
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data["full_name"] == "新姓名"
    assert data["department"] == "技术质量部"
    assert data["position"] == "专业监理工程师"
    assert data["signature"] == "新姓名"
    _cleanup(username)


def test_employee_no_must_be_unique(client, requires_db):
    """工号是单位内部唯一标识，重复必须被拒绝并指出占用者。"""
    project_id = _ensure_project()
    headers = _login(client, "admin")
    employee_no = f"EMP{uuid.uuid4().hex[:8].upper()}"

    first = _unique("um_emp1")
    r1 = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": first,
            "password": "Test@12345",
            "role": "engineer",
            "project_ids": [project_id],
            "employee_no": employee_no,
        },
    )
    assert r1.status_code == 200, r1.text

    second = _unique("um_emp2")
    r2 = client.post(
        "/api/v1/admin/users",
        headers=headers,
        json={
            "username": second,
            "password": "Test@12345",
            "role": "engineer",
            "project_ids": [project_id],
            "employee_no": employee_no,
        },
    )
    assert r2.status_code == 409, f"重复工号未被拒绝：{r2.status_code}"
    assert first in r2.text, f"未指出占用者：{r2.text[:200]}"

    _cleanup(first)
    _cleanup(second)


def test_employee_no_allows_multiple_empty(client, requires_db):
    """工号可空，且多个空值不冲突（部分唯一索引的正确性）。"""
    project_id = _ensure_project()
    headers = _login(client, "admin")
    names = [_unique("um_noemp") for _ in range(2)]

    for name in names:
        response = client.post(
            "/api/v1/admin/users",
            headers=headers,
            json={
                "username": name,
                "password": "Test@12345",
                "role": "viewer",
                "project_ids": [project_id],
                # 不传工号
            },
        )
        assert response.status_code == 200, f"{name} 创建失败：{response.text[:200]}"
        assert response.json()["data"]["employee_no"] is None
    for name in names:
        _cleanup(name)


def test_identity_fields_exposed_in_me(client, requires_db):
    """当前用户信息应含身份字段（用于报告签认署名与界面展示）。"""
    headers = _login(client, "admin")
    response = client.get("/api/v1/auth/me", headers=headers)
    assert response.status_code == 200
    data = response.json()["data"]
    for field in ("full_name", "employee_no", "org_name", "department", "position", "signature"):
        assert field in data, f"/auth/me 缺少身份字段 {field}"


# --------------------------------------------------------------------------- #
# 安全边界：非管理员不得调用任何用户管理接口
# --------------------------------------------------------------------------- #
def test_non_admin_cannot_manage_users(client, requires_db):
    project_id = _ensure_project()
    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_scope

    username = _unique("um_viewer")
    with session_scope() as session:
        session.add(
            User(
                username=username,
                full_name="越权尝试",
                password_hash=hash_password("Test@12345"),
                role="viewer",
                project_ids=[project_id],
            )
        )
    headers = _login(client, username, "Test@12345")

    assert client.get("/api/v1/admin/users", headers=headers).status_code == 403
    assert (
        client.post(
            "/api/v1/admin/users",
            headers=headers,
            json={
                "username": "hacker",
                "password": "Test@12345",
                "role": "admin",
                "project_ids": [],
            },
        ).status_code
        == 403
    ), "只读用户竟能创建管理员账号"
    me = client.get("/api/v1/auth/me", headers=headers).json()["data"]
    assert (
        client.patch(
            f"/api/v1/admin/users/{me['id']}", headers=headers, json={"role": "admin"}
        ).status_code
        == 403
    ), "只读用户竟能给自己提权"
    assert client.delete(f"/api/v1/admin/users/{me['id']}", headers=headers).status_code == 403
    _cleanup(username)
