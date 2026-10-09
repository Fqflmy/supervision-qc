# -*- coding: utf-8 -*-
"""越权回归：只读角色不得触发评估任务执行或提交复核反馈。

这两处都属于「同类端点强度不一致」—— 创建任务、人工介入（resume）都要求
写权限，但执行/提交与复核反馈只校验了「能否访问该任务」，漏了「能否写」。

后果很实在：
- 只读用户能触发 Agent 执行 → **消耗真实 LLM Token（真金白银）**；
- 只读用户能提交复核意见 → **污染质量记录**。

本文件同时钉住「读权限不等于写权限」这条边界。
"""
from __future__ import annotations

import uuid

import pytest


def _login(client, username: str, password: str = "Admin@12345"):
    response = client.post(
        "/api/v1/auth/login", json={"username": username, "password": password}
    )
    if response.status_code != 200:
        pytest.skip(f"{username} 登录失败，跳过")
    return {"Authorization": f"Bearer {response.json()['data']['access_token']}"}


def _ensure_user(client, username: str, role: str, project_id: int | None = None) -> None:
    """确保测试账号存在（幂等）。"""
    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_scope

    with session_scope() as session:
        row = session.query(User).filter(User.username == username).one_or_none()
        if row is None:
            session.add(
                User(
                    username=username,
                    full_name=f"越权测试-{role}",
                    password_hash=hash_password("Admin@12345"),
                    role=role,
                    project_ids=[project_id] if project_id else [],
                )
            )
        elif project_id and project_id not in (row.project_ids or []):
            row.project_ids = sorted({*(row.project_ids or []), project_id})


def _seed_project_and_task(owner_username: str) -> tuple[int, str]:
    """建一个项目 + 该项目下由 owner 发起的任务，返回 (project_id, task_id)。"""
    from app.db.models import EvalTask, Project, User
    from app.db.session import session_scope

    with session_scope() as session:
        project = session.query(Project).order_by(Project.id).first()
        if project is None:
            project = Project(code="AUTHZ-T1", name="越权测试项目", specialty="结构工程")
            session.add(project)
            session.flush()

        owner = session.query(User).filter(User.username == owner_username).one_or_none()
        if owner is None:
            raise RuntimeError(f"缺少 owner 账号 {owner_username}")

        task = (
            session.query(EvalTask)
            .filter(EvalTask.project_id == project.id, EvalTask.user_id == owner.id)
            .first()
        )
        if task is None:
            task = EvalTask(
                id=uuid.uuid4(),
                project_id=project.id,
                user_id=owner.id,
                title="越权测试任务",
                eval_type="inspection_lot",
                specialty="结构工程",
                current_state="COMPLETED",
                iteration_count=1,
            )
            session.add(task)
            session.flush()
        return project.id, str(task.id)


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


# --------------------------------------------------------------------------- #
# 触发任务执行需要写权限
# --------------------------------------------------------------------------- #
def test_viewer_cannot_run_task_even_with_read_access(client, requires_db):
    """只读用户即使能**看到**任务，也不能触发执行（会消耗 LLM Token）。"""
    project_id, task_id = _seed_project_and_task("engineer")
    _ensure_user(client, "authz_viewer", "viewer", project_id)

    headers = _login(client, "authz_viewer")
    # 前提：该任务对他可见（同项目）
    detail = client.get(f"/api/v1/eval/tasks/{task_id}", headers=headers)
    assert detail.status_code == 200, "前置条件不成立：viewer 应能看到本项目任务"

    # 但不能触发执行
    run = client.post(f"/api/v1/eval/tasks/{task_id}/run", headers=headers)
    assert run.status_code == 403, (
        f"只读角色竟能触发任务执行（{run.status_code}）—— 会消耗真实 LLM Token"
    )

    submit = client.post(f"/api/v1/eval/tasks/{task_id}/submit", headers=headers)
    assert submit.status_code == 403, (
        f"只读角色竟能提交任务执行（{submit.status_code}）—— 会消耗真实 LLM Token"
    )


def test_engineer_can_run_task(client, requires_db):
    """对照组：监理工程师有 eval:write，应通过鉴权（业务状态另说）。"""
    _, task_id = _seed_project_and_task("engineer")
    headers = _login(client, "engineer")
    # 任务已是终态，允许用 resume=true 绕开「终态不可重跑」的业务限制，
    # 从而只验证鉴权层 —— 若不是 403 即说明写权限校验通过。
    run = client.post(
        f"/api/v1/eval/tasks/{task_id}/run?resume=true", headers=headers
    )
    assert run.status_code != 403, f"工程师被误拦：{run.status_code} {run.text[:200]}"


# --------------------------------------------------------------------------- #
# 提交复核反馈需要复核权限
# --------------------------------------------------------------------------- #
def test_viewer_cannot_submit_feedback(client, requires_db):
    """只读用户不得提交人工复核意见（否则会污染质量记录）。"""
    project_id, task_id = _seed_project_and_task("engineer")
    _ensure_user(client, "authz_viewer", "viewer", project_id)

    headers = _login(client, "authz_viewer")
    response = client.post(
        "/api/v1/eval/feedback",
        headers=headers,
        json={
            "task_id": task_id,
            "action": "confirm",
            "dimension": "conclusion_reasonableness",
            "original_value": "1",
            "corrected_value": "0",
            "comment": "只读用户不应能提交",
        },
    )
    assert response.status_code == 403, (
        f"只读角色竟能提交复核反馈（{response.status_code}）—— 会污染质量记录"
    )


def test_expert_can_submit_feedback(client, requires_db):
    """对照组：审核人员有 eval:review，应可提交。"""
    project_id, task_id = _seed_project_and_task("engineer")
    _ensure_user(client, "authz_expert", "expert", project_id)

    headers = _login(client, "authz_expert")
    response = client.post(
        "/api/v1/eval/feedback",
        headers=headers,
        json={
            "task_id": task_id,
            "action": "confirm",
            "dimension": "conclusion_reasonableness",
            "original_value": "1",
            "corrected_value": "1",
            "comment": "审核人员确认",
        },
    )
    assert response.status_code != 403, f"审核人员被误拦：{response.status_code}"


# --------------------------------------------------------------------------- #
# 创建任务：单项目自动采用（修复「工程师发不起评估」）
# --------------------------------------------------------------------------- #
def test_me_projects_returns_authorized_projects(client, requires_db):
    """普通用户应能查到**自己**被授权的项目（含名称），否则无法创建任务。"""
    project_id, _ = _seed_project_and_task("engineer")
    _ensure_user(client, "authz_engineer", "engineer", project_id)

    headers = _login(client, "authz_engineer")
    response = client.get("/api/v1/me/projects", headers=headers)
    assert response.status_code == 200, response.text

    data = response.json()["data"]
    ids = [item["id"] for item in data["items"]]
    assert project_id in ids, f"被授权项目未出现在清单中：{ids}"
    # 必须有名称 —— 只给 ID 用户无从选择（这正是原始缺陷）
    for item in data["items"]:
        assert item.get("name"), f"项目 {item.get('id')} 缺少名称"
    assert data["can_auto_select"] == (len(ids) == 1)


def test_engineer_creates_task_without_project_id(client, requires_db):
    """**核心回归**：工程师只被授权一个项目时，无需传 project_id 也能创建任务。

    修复前该请求返回 403「非管理员创建任务必须指定 project_id」，
    而用户无从得知该传什么 —— 主流程实际走不通。
    """
    project_id, _ = _seed_project_and_task("engineer")
    _ensure_user(client, "authz_engineer", "engineer", project_id)

    headers = _login(client, "authz_engineer")
    response = client.post(
        "/api/v1/eval/tasks",
        headers=headers,
        json={
            "eval_type": "inspection_lot",
            "specialty": "结构工程",
            "title": "单项目自动归属测试",
            "object": {"part": "现浇混凝土结构", "description": "验证单项目自动采用"},
        },
    )
    assert response.status_code in (200, 201), (
        f"工程师仍无法创建任务：{response.status_code} {response.text[:250]}"
    )

    # 创建出的任务应落在其被授权项目下，且自己可见
    task_id = response.json()["data"]["task_id"]
    detail = client.get(f"/api/v1/eval/tasks/{task_id}", headers=headers)
    assert detail.status_code == 200, "创建后自己看不到该任务"
    assert detail.json()["data"]["project_id"] == project_id, (
        "任务未归属到被授权的项目，数据隔离会错位"
    )


def test_non_admin_cannot_create_task_in_unauthorized_project(client, requires_db):
    """隔离不能被「自动采用」放松：显式传他人项目仍须拒绝。"""
    from app.db.models import Project
    from app.db.session import session_scope

    project_id, _ = _seed_project_and_task("engineer")
    _ensure_user(client, "authz_engineer", "engineer", project_id)

    with session_scope() as session:
        other = (
            session.query(Project).filter(Project.id != project_id).order_by(Project.id).first()
        )
        if other is None:
            session.add(Project(code="AUTHZ-T2", name="未授权项目", specialty="结构工程"))
            session.flush()
            other_id = None  # 需要在下一会话查询
        else:
            other_id = other.id

    if other_id is None:
        with session_scope() as session:
            other_id = (
                session.query(Project).filter(Project.code == "AUTHZ-T2").one().id
            )

    headers = _login(client, "authz_engineer")
    response = client.post(
        "/api/v1/eval/tasks",
        headers=headers,
        json={
            "project_id": other_id,
            "eval_type": "inspection_lot",
            "specialty": "结构工程",
            "title": "越权项目测试",
            "object": {"part": "越权", "description": "应被拒绝"},
        },
    )
    assert response.status_code == 403, (
        f"竟能在未授权项目下创建任务：{response.status_code} {response.text[:200]}"
    )
