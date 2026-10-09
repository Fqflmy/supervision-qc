# -*- coding: utf-8 -*-
"""角色权限矩阵与「按角色显示不同界面」的回归测试。

覆盖两件事：
1. **前后端权限表一致**：前端的 ``utils/permissions.ts`` 镜像了后端的
   ``ROLE_PERMISSIONS``，用它推导菜单与按钮可用性。两边一旦分叉，就会出现
   「菜单能点但接口 403」这类难查问题。这里通过 Node 脚本逐项比对。
2. **指标口径与列表一致**：``/metrics`` 必须按调用者可见范围统计，
   否则会出现「卡片 80 个任务、列表一条都没有」的矛盾。
"""
from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
CHECK_SCRIPT = ROOT / "frontend" / "scripts" / "check-role-matrix.mjs"


def _node() -> str | None:
    return shutil.which("node")


# --------------------------------------------------------------------------- #
# 前后端权限一致 + 各角色菜单
# --------------------------------------------------------------------------- #
def test_role_matrix_matches_backend():
    """前端权限表与后端 ROLE_PERMISSIONS 必须一致，且各角色菜单符合契约。"""
    node = _node()
    if not node:
        pytest.skip("未找到 node，跳过前端权限矩阵校验")
    if not CHECK_SCRIPT.exists():
        pytest.skip(f"缺少 {CHECK_SCRIPT}")

    result = subprocess.run(
        [node, str(CHECK_SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    output = (result.stdout or "") + (result.stderr or "")
    assert result.returncode == 0, f"权限矩阵校验失败：\n{output}"


def test_permissions_source_defines_all_backend_roles():
    """前端 permissions.ts 必须覆盖后端所有角色（漏一个该角色就没菜单）。"""
    import re

    backend_source = (BACKEND / "app" / "api" / "deps.py").read_text(encoding="utf-8")
    frontend_source = (ROOT / "frontend" / "src" / "utils" / "permissions.ts").read_text(
        encoding="utf-8"
    )

    backend_block = re.search(r"ROLE_PERMISSIONS[^{]*\{([\s\S]*?)\n\}", backend_source)
    assert backend_block, "未能解析后端 ROLE_PERMISSIONS"
    backend_roles = set(re.findall(r'"([a-z_]+)"\s*:\s*\{', backend_block.group(1)))

    frontend_block = re.search(r"ROLE_PERMISSIONS[^{]*=\s*\{([\s\S]*?)\n\}", frontend_source)
    assert frontend_block, "未能解析前端 ROLE_PERMISSIONS"
    frontend_roles = set(re.findall(r"([a-z_]+)\s*:\s*\[", frontend_block.group(1)))

    missing = backend_roles - frontend_roles
    assert not missing, f"前端缺少这些角色：{sorted(missing)}"
    extra = frontend_roles - backend_roles
    assert not extra, f"前端多出后端没有的角色：{sorted(extra)}"


# --------------------------------------------------------------------------- #
# 指标口径
# --------------------------------------------------------------------------- #
def test_metrics_response_declares_scope(client, requires_db):
    """``/metrics`` 应返回 scope 字段，前端据此提示是否仅可见范围。"""
    login = client.post(
        "/api/v1/auth/login", json={"username": "admin", "password": "Admin@12345"}
    )
    if login.status_code != 200:
        pytest.skip("admin 登录失败，跳过")
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
    response = client.get("/api/v1/metrics", headers=headers)
    assert response.status_code == 200, response.text
    data = response.json()["data"]
    assert data.get("scope") == "global", "管理员应为全局口径"
    for key in ("documents_total", "chunks_total", "tasks_by_state", "tokens_total"):
        assert key in data, f"指标缺少 {key}"


def test_metrics_works_for_every_role(client, requires_db):
    """**每个角色**调用 /metrics 都必须成功。

    这是回归测试：按可见范围过滤时曾用错列名（DocChunk.version_id 实际是
    doc_version_id），导致非管理员调用直接 500 —— 表现为页面上
    「运行指标加载失败」，而管理员看不到这个问题。
    按角色逐个跑一遍是最直接的防线。
    """
    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_scope

    roles = ["admin", "engineer", "expert", "viewer"]
    with session_scope() as session:
        for role in roles:
            username = f"metrics_{role}"
            row = session.query(User).filter(User.username == username).one_or_none()
            if row is None:
                session.add(
                    User(
                        username=username,
                        password_hash=hash_password("Test@12345"),
                        role=role,
                        project_ids=[],
                    )
                )

    for role in roles:
        username = f"metrics_{role}"
        login = client.post(
            "/api/v1/auth/login", json={"username": username, "password": "Test@12345"}
        )
        assert login.status_code == 200, f"{role} 登录失败：{login.text[:200]}"
        headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}
        response = client.get("/api/v1/metrics", headers=headers)
        assert response.status_code == 200, f"{role} 调用 /metrics 失败：{response.text[:300]}"

        expected_scope = "global" if role == "admin" else "visible"
        assert response.json()["data"].get("scope") == expected_scope


def test_metrics_scope_is_not_global_for_engineer(client, requires_db):
    """非管理员的指标不应把其他项目的数据算进来（口径与列表一致）。"""
    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_scope

    with session_scope() as session:
        row = (
            session.query(User)
            .filter(User.username == "metrics_engineer")
            .one_or_none()
        )
        if row is None:
            session.add(
                User(
                    username="metrics_engineer",
                    password_hash=hash_password("Test@12345"),
                    role="engineer",
                    project_ids=[],
                )
            )

    login = client.post(
        "/api/v1/auth/login", json={"username": "metrics_engineer", "password": "Test@12345"}
    )
    headers = {"Authorization": f"Bearer {login.json()['data']['access_token']}"}

    metrics = client.get("/api/v1/metrics", headers=headers).json()["data"]
    scoped_tasks = sum(int(v) for v in metrics["tasks_by_state"].values())

    # 同一身份的任务列表（按可见范围）应与指标里的任务总数一致
    listing = client.get("/api/v1/eval/tasks?page_size=1", headers=headers).json()["data"]
    assert scoped_tasks == listing["meta"]["total"], (
        f"指标任务数 {scoped_tasks} 与列表总数 {listing['meta']['total']} 不一致 —— "
        "两处口径必须相同，否则会出现「卡片有数字、列表是空的」"
    )


def test_prometheus_metrics_stay_global(monkeypatch):
    """Prometheus 端点必须保持全局口径，不能按用户过滤。

    它用于监控抓取，没有「当前用户」的概念；若被改成按可见范围统计，
    监控面板的数字会随抓取方身份变化，失去意义。
    """
    source = (BACKEND / "app" / "api" / "routes" / "metrics.py").read_text(encoding="utf-8")
    # collect_metrics 不接受 user 参数（无用户上下文）
    assert "def collect_metrics(session" in source
    assert "visible_task_filter" not in source, "Prometheus 端点不应按用户可见范围过滤"


@pytest.fixture(scope="module")
def client():
    from fastapi.testclient import TestClient

    from app.main import app

    with TestClient(app) as test_client:
        yield test_client
