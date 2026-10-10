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


@pytest.fixture(scope="module", autouse=True)
def _setup_accounts():
    """确保本文件用到的专用账号存在。

    不依赖种子账号（`engineer`）：种子账号的项目授权会被其他用例改动，
    导致「只授权一个项目」这类前置条件失效（实测踩过）。
    """
    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_scope

    with session_scope() as session:
        for username, role in [
            ("authz_owner", "engineer"),
            ("authz_foreign_owner", "engineer"),
        ]:
            row = session.query(User).filter(User.username == username).one_or_none()
            if row is None:
                session.add(
                    User(
                        username=username,
                        full_name=f"越权测试-{role}",
                        password_hash=hash_password("Admin@12345"),
                        role=role,
                        project_ids=[],
                    )
                )
    yield


def _ensure_user(client, username: str, role: str, project_id: int | None = None) -> None:
    """确保测试账号存在，并把授权项目**精确设为** project_id（可为空）。

    ⚠️ 必须「精确设置」而不是「追加」：追加会让用例间互相污染 ——
    前一个用例授权了项目 A，后一个用例又加 B，于是「只授权一个项目」
    的前置条件不再成立（实测正是如此：报「被授权 2 个项目」）。
    """
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
        else:
            row.project_ids = [project_id] if project_id else []


def _seed_project_and_task(owner_username: str) -> tuple[int, str]:
    """建一个项目 + 该项目下由 owner 发起的任务，返回 (project_id, task_id)。

    ⚠️ 用**专用 code** 定位项目，不要取「第一个项目」——
    测试库中可能有其他用例创建的项目，取第一个会让本用例的任务
    落在与 `_ensure_user` 授权不同的项目下，造成假失败。
    """
    from app.db.models import EvalTask, Project, User
    from app.db.session import session_scope

    with session_scope() as session:
        project = session.query(Project).filter(Project.code == "AUTHZ-T1").one_or_none()
        if project is None:
            project = Project(code="AUTHZ-T1", name="越权测试项目", specialty="结构工程")
            session.add(project)
            session.flush()

        owner = session.query(User).filter(User.username == owner_username).one_or_none()
        if owner is None:
            raise RuntimeError(f"缺少 owner 账号 {owner_username}")

        # owner 必须被授权到该项目，否则创建出的任务连他自己都看不到
        # （`assert_task_access` 会拒），后续用例全部失效。
        if project.id not in (owner.project_ids or []):
            owner.project_ids = sorted({*(owner.project_ids or []), project.id})

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


def _seed_task_with_report(owner_username: str = "authz_owner") -> tuple[int, str]:
    """建项目 + 任务 + **报告**，返回 (project_id, task_id)。

    只读用户查看报告的用例需要真实存在的报告，仅有任务会得到 404。
    """
    from app.db.models import EvalReport, EvalTask, Project, User
    from app.db.session import session_scope

    with session_scope() as session:
        project = session.query(Project).filter(Project.code == "AUTHZ-T1").one_or_none()
        if project is None:
            project = Project(code="AUTHZ-T1", name="越权测试项目", specialty="结构工程")
            session.add(project)
            session.flush()

        owner = session.query(User).filter(User.username == owner_username).one()
        if project.id not in (owner.project_ids or []):
            owner.project_ids = sorted({*(owner.project_ids or []), project.id})

        task = EvalTask(
            id=uuid.uuid4(),
            project_id=project.id,
            user_id=owner.id,
            title="只读报告访问测试任务",
            eval_type="inspection_lot",
            specialty="结构工程",
            current_state="COMPLETED",
            iteration_count=2,
            version=1,
        )
        session.add(task)
        session.flush()
        session.add(
            EvalReport(
                id=uuid.uuid4(),
                task_id=task.id,
                conclusion="混凝土强度满足设计要求，入模温度超限需整改。",
                overall_verdict="non_compliant",
                risk_level="high",
                summary="测试报告",
                content={"matches": []},
                markdown="# 测试报告\n\n混凝土入模温度 35℃ 超出限值。",
                basis_count=3,
                non_compliance_count=1,
                generator_model="deepseek-chat",
                human_verdict=None,
                is_final=False,
            )
        )
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
    project_id, task_id = _seed_project_and_task("authz_owner")
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
    _, task_id = _seed_project_and_task("authz_owner")
    # 必须用**该任务的 owner**（authz_owner）登录：
    # 种子账号 engineer 未被授权到 AUTHZ-T1 项目，会因归属校验被拒，
    # 那是隔离生效（正确），而非鉴权误拦。
    headers = _login(client, "authz_owner")
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
    project_id, task_id = _seed_project_and_task("authz_owner")
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
    project_id, task_id = _seed_project_and_task("authz_owner")
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
    project_id, _ = _seed_project_and_task("authz_owner")
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
    project_id, _ = _seed_project_and_task("authz_owner")
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

    project_id, _ = _seed_project_and_task("authz_owner")
    _ensure_user(client, "authz_engineer", "engineer", project_id)

    with session_scope() as session:
        # 取一个**不在**该用户授权列表中的项目
        other = (
            session.query(Project)
            .filter(Project.id != project_id)
            .order_by(Project.id)
            .first()
        )
        if other is None:
            other = Project(code="AUTHZ-T2", name="未授权项目", specialty="结构工程")
            session.add(other)
            session.flush()
        other_id = other.id

    assert other_id != project_id
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


# --------------------------------------------------------------------------- #
# 只读用户可查看授权项目内的评估报告（但仅限只读）
# --------------------------------------------------------------------------- #
def test_viewer_can_read_report_in_authorized_project(client, requires_db):
    """普通用户应能查看**授权项目内**的评估报告 —— 这是其核心用途。

    缺陷背景：后端一直放行 `eval:read`，但前端没有任何入口
    （菜单只有「规范查询 / 智能问答」），属「有权限没入口」。
    """
    project_id, task_id = _seed_task_with_report("authz_owner")
    _ensure_user(client, "authz_viewer", "viewer", project_id)

    headers = _login(client, "authz_viewer")

    # 列表能看到
    listing = client.get("/api/v1/eval/tasks?page_size=100", headers=headers)
    assert listing.status_code == 200, listing.text
    ids = [i["id"] for i in listing.json()["data"]["items"]]
    assert task_id in ids, "授权项目内的任务未出现在普通用户的列表中"

    # 报告能读，且内容完整（结论 / 风险 / 正文）
    report = client.get(f"/api/v1/eval/tasks/{task_id}/report", headers=headers)
    assert report.status_code == 200, report.text
    data = report.json()["data"]
    assert data.get("overall_verdict"), "报告缺少总体结论"
    assert data.get("markdown") or data.get("content"), "报告缺少正文内容"
    # 复核状态也要能看到（只读用户需要知道报告是否已签发）
    assert "review" in data, "报告缺少人工裁定信息"


def test_viewer_cannot_write_even_in_authorized_project(client, requires_db):
    """「能读报告」不等于「能写」—— 授权项目内同样禁止写操作。"""
    project_id, task_id = _seed_project_and_task("authz_owner")
    _ensure_user(client, "authz_viewer", "viewer", project_id)
    headers = _login(client, "authz_viewer")

    # 不能触发执行
    assert client.post(f"/api/v1/eval/tasks/{task_id}/run", headers=headers).status_code == 403
    # 不能裁定
    assert (
        client.post(
            f"/api/v1/eval/tasks/{task_id}/review",
            headers=headers,
            json={"verdict": "qualified", "comment": "越权"},
        ).status_code
        == 403
    )
    # 不能在授权项目下创建任务（viewer 无 eval:write）
    assert (
        client.post(
            "/api/v1/eval/tasks",
            headers=headers,
            json={
                "project_id": project_id,
                "eval_type": "inspection_lot",
                "specialty": "结构工程",
                "title": "越权创建",
                "object": {"part": "越权"},
            },
        ).status_code
        == 403
    )


def test_viewer_cannot_read_report_outside_authorized_project(client, requires_db):
    """跨项目读取报告必须被拒 —— 只读权限仍受项目隔离约束。

    ⚠️ 「他人项目」的任务必须由**另一个 owner** 创建。
    `can_access_task` 的设计允许「自己发起的任务」跨项目访问
    （合理：发起人应始终能看到自己的数据），因此若仍用同一 owner，
    隔离校验会按设计放行 —— 那是测试设计错误，不是越权。
    """
    from app.db.models import EvalReport, EvalTask, Project, User
    from app.db.session import session_scope

    project_id, _ = _seed_project_and_task("authz_owner")
    _ensure_user(client, "authz_viewer", "viewer", project_id)

    # 造一个**别的项目**下的任务+报告，且归属**另一个用户**
    with session_scope() as session:
        other_project = (
            session.query(Project).filter(Project.id != project_id).order_by(Project.id).first()
        )
        if other_project is None:
            other_project = Project(code="AUTHZ-T9", name="他人项目", specialty="结构工程")
            session.add(other_project)
            session.flush()

        foreign_owner = (
            session.query(User).filter(User.username == "authz_foreign_owner").one()
        )
        foreign = EvalTask(
            id=uuid.uuid4(),
            project_id=other_project.id,
            user_id=foreign_owner.id,
            title="他人项目的任务",
            eval_type="inspection_lot",
            specialty="结构工程",
            current_state="COMPLETED",
            version=1,
        )
        session.add(foreign)
        session.flush()
        session.add(
            EvalReport(
                id=uuid.uuid4(),
                task_id=foreign.id,
                conclusion="他人项目报告",
                overall_verdict="qualified",
                content={"matches": []},
                is_final=False,
            )
        )
        foreign_id = str(foreign.id)

    headers = _login(client, "authz_viewer")

    assert client.get(f"/api/v1/eval/tasks/{foreign_id}", headers=headers).status_code in (
        403,
        404,
    ), "只读用户竟能读取他人项目的任务详情"
    assert client.get(f"/api/v1/eval/tasks/{foreign_id}/report", headers=headers).status_code in (
        403,
        404,
    ), "只读用户竟能读取他人项目的报告"
