# -*- coding: utf-8 -*-
"""报告 PDF 导出（可交付形态）。

覆盖：
- 内容：真的是 PDF、含中文正文、含签发状态与页码；
- 文件名：中文走 RFC 5987 的 ``filename*``，``filename=`` 必须是 ASCII 回退
  （HTTP 头只能 latin-1，中文直接放进 ``filename=`` 会让响应头构造抛
  ``UnicodeEncodeError`` → 接口 500）；
- **权限隔离**：未登录 401、跨项目 403（PDF 比 JSON 更严重，会被转发出去）；
- 审计：导出必须留痕（谁、何时、哪份、当时是否已签发）。
"""
from __future__ import annotations

import io
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


def _seed_report_task(owner_username: str = "engineer", title: str = "PDF 导出测试报告"):
    """造一个带报告与签发的任务，返回 (task_id, project_id, owner_id)。"""
    from app.db.models import EvalReport, EvalTask, Project, User
    from app.db.session import session_scope

    with session_scope() as session:
        project = session.query(Project).order_by(Project.id).first()
        if project is None:
            pytest.skip("库中没有项目")
        owner = session.query(User).filter(User.username == owner_username).one_or_none()
        if owner is None:
            pytest.skip(f"缺少 {owner_username} 账号")

        task = EvalTask(
            id=uuid.uuid4(),
            project_id=project.id,
            user_id=owner.id,
            title=title,
            eval_type="inspection_lot",
            specialty="结构工程",
            current_state="COMPLETED",
            version=1,
        )
        session.add(task)
        session.flush()
        session.add(
            EvalReport(
                task_id=task.id,
                overall_verdict="non_compliant",
                risk_level="high",
                markdown=(
                    "# 测试工程地下室剪力墙结构工程质量评估报告\n\n"
                    "## 一、评估概述\n\n"
                    "| 项目 | 内容 |\n| --- | --- |\n"
                    "| 项目名称 | 测试工程 |\n| 评估专业 | 结构工程 |\n\n"
                    "受建设单位委托开展质量评估，范围为混凝土入模温度控制。\n\n"
                    "## 二、条款逐条比对\n\n"
                    "| 序号 | 核查项 | 判定 | 判定理由 |\n| --- | --- | --- | --- |\n"
                    "| 1 | 入模温度核查 | 不符合 | 实测 35℃ 超出 GB 50204-2015 第 5.3.3 条 30℃ 限值 |\n\n"
                    "## 三、总体结论\n\n- 总体判定：**不符合**\n- 风险等级：**高**\n"
                ),
                basis_count=1,
                non_compliance_count=1,
                human_verdict="qualified",
                is_final=True,
                review_comment="已核对引用条款",
            )
        )
        return str(task.id), project.id, owner.id


def _cleanup(task_id: str) -> None:
    from app.db.models import EvalReport, EvalTask
    from app.db.session import session_scope

    with session_scope() as session:
        report = (
            session.query(EvalReport).filter(EvalReport.task_id == uuid.UUID(task_id)).one_or_none()
        )
        if report is not None:
            session.delete(report)
        task = session.get(EvalTask, uuid.UUID(task_id))
        if task is not None:
            session.delete(task)


# --------------------------------------------------------------------------- #
# 内容
# --------------------------------------------------------------------------- #
def test_pdf_export_returns_valid_pdf(client, requires_db):
    task_id, _, _ = _seed_report_task()
    headers = _login(client, "engineer")

    response = client.get(f"/api/v1/eval/tasks/{task_id}/report/pdf", headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("application/pdf")
    assert response.content[:5] == b"%PDF-", "不是有效 PDF"
    assert len(response.content) > 3000, f"体积过小：{len(response.content)}"
    _cleanup(task_id)


def test_pdf_contains_chinese_body_and_signoff(client, requires_db):
    """PDF 必须真的渲染出中文正文与签发状态。

    只断言 HTTP 200 会漏掉「返回空 PDF」；签发状态必须印在文件上 ——
    PDF 一旦导出就脱离系统，接收方无从查询系统状态。
    """
    pdfplumber = pytest.importorskip("pdfplumber")

    task_id, _, _ = _seed_report_task()
    headers = _login(client, "engineer")
    response = client.get(f"/api/v1/eval/tasks/{task_id}/report/pdf", headers=headers)
    assert response.status_code == 200

    with pdfplumber.open(io.BytesIO(response.content)) as pdf:
        text = "\n".join((p.extract_text() or "") for p in pdf.pages)

    assert "评估" in text and "工程" in text, "中文正文未渲染"
    assert "不符合" in text, "评估结论未渲染"
    assert "已签发" in text or "未签发" in text, "签发状态未印在文件上"
    assert "第 1 页" in text, "页码缺失"
    _cleanup(task_id)


def test_pdf_filename_header_is_latin1_safe(client, requires_db):
    """⚠️ HTTP 头只能 latin-1：中文必须只出现在 ``filename*=UTF-8''`` 里。

    若把中文放进 ``filename="..."``，Starlette 构造响应头时会抛
    ``UnicodeEncodeError``，接口直接 500（实际踩到过）。
    """
    task_id, _, _ = _seed_report_task()
    headers = _login(client, "engineer")

    response = client.get(f"/api/v1/eval/tasks/{task_id}/report/pdf", headers=headers)
    assert response.status_code == 200

    disposition = response.headers.get("content-disposition", "")
    assert "attachment" in disposition
    assert "filename*=UTF-8''" in disposition, "缺少 RFC 5987 的 UTF-8 文件名"

    plain = ""
    if 'filename="' in disposition:
        plain = disposition.split('filename="', 1)[1].split('"', 1)[0]
    assert plain, "缺少 ASCII 回退文件名"
    assert plain.isascii(), f"ASCII 回退名含非 ASCII 字符：{plain!r}"
    _cleanup(task_id)


# --------------------------------------------------------------------------- #
# 权限隔离
# --------------------------------------------------------------------------- #
def test_pdf_requires_authentication(client, requires_db):
    task_id, _, _ = _seed_report_task()
    response = client.get(f"/api/v1/eval/tasks/{task_id}/report/pdf")
    assert response.status_code == 401
    _cleanup(task_id)


def test_pdf_cross_project_is_forbidden(client, requires_db):
    """跨项目导出必须拒绝。

    PDF 比 JSON 更严重：文件会被转发、打印、长期保存，
    一旦越权导出，事后无法收回。
    """
    from app.core.security import hash_password
    from app.db.models import Project, User
    from app.db.session import session_scope

    task_id, project_id, _ = _seed_report_task()

    # 造一个只被授权到「另一个项目」的用户
    outsider = f"pdf_out_{uuid.uuid4().hex[:6]}"
    with session_scope() as session:
        other = session.query(Project).filter(Project.id != project_id).first()
        if other is None:
            other = Project(
                code=f"PDF-{uuid.uuid4().hex[:6].upper()}",
                name="PDF 越权测试项目",
                specialty="结构工程",
            )
            session.add(other)
            session.flush()
        session.add(
            User(
                username=outsider,
                full_name="越权测试",
                password_hash=hash_password("Test@12345"),
                role="viewer",
                project_ids=[other.id],
            )
        )

    headers = _login(client, outsider, "Test@12345")
    response = client.get(f"/api/v1/eval/tasks/{task_id}/report/pdf", headers=headers)
    assert response.status_code == 403, f"跨项目导出未被拒绝：{response.status_code}"

    _cleanup(task_id)
    with session_scope() as session:
        row = session.query(User).filter(User.username == outsider).one_or_none()
        if row is not None:
            session.delete(row)


def test_pdf_viewer_can_export_within_authorized_project(client, requires_db):
    """只读用户在授权项目内**可以**导出（需求：普通用户能看授权项目的报告）。"""
    task_id, project_id, _ = _seed_report_task()

    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_scope

    username = f"pdf_view_{uuid.uuid4().hex[:6]}"
    with session_scope() as session:
        session.add(
            User(
                username=username,
                full_name="只读导出测试",
                password_hash=hash_password("Test@12345"),
                role="viewer",
                project_ids=[project_id],
            )
        )
    headers = _login(client, username, "Test@12345")
    response = client.get(f"/api/v1/eval/tasks/{task_id}/report/pdf", headers=headers)
    assert response.status_code == 200, response.text

    _cleanup(task_id)
    from app.db.models import User as U
    from app.db.session import session_scope as ss

    with ss() as session:
        row = session.query(U).filter(U.username == username).one_or_none()
        if row is not None:
            session.delete(row)


def test_pdf_missing_report_returns_404(client, requires_db):
    from app.db.models import EvalTask, Project, User
    from app.db.session import session_scope

    with session_scope() as session:
        project = session.query(Project).order_by(Project.id).first()
        owner = session.query(User).filter(User.username == "engineer").one()
        task = EvalTask(
            id=uuid.uuid4(),
            project_id=project.id,
            user_id=owner.id,
            title="无报告任务",
            eval_type="inspection_lot",
            current_state="FAILED",
            version=1,
        )
        session.add(task)
        task_id = str(task.id)

    headers = _login(client, "engineer")
    response = client.get(f"/api/v1/eval/tasks/{task_id}/report/pdf", headers=headers)
    assert response.status_code == 404
    _cleanup(task_id)


# --------------------------------------------------------------------------- #
# 审计
# --------------------------------------------------------------------------- #
def test_pdf_export_is_audited(client, requires_db):
    """导出必须留痕：报告涉及质量责任，需追溯「谁何时导出了哪份」。"""
    from sqlalchemy import select

    from app.db.models import AuditLog, User
    from app.db.session import session_scope

    task_id, _, owner_id = _seed_report_task()
    headers = _login(client, "engineer")

    response = client.get(f"/api/v1/eval/tasks/{task_id}/report/pdf", headers=headers)
    assert response.status_code == 200

    with session_scope() as session:
        logs = (
            session.execute(
                select(AuditLog)
                .where(AuditLog.action == "report_export")
                .where(AuditLog.user_id == owner_id)
                .order_by(AuditLog.id.desc())
                .limit(5)
            )
            .scalars()
            .all()
        )
    assert logs, "导出未写入审计日志"
    detail = str(logs[0].detail or "")
    assert "pdf" in detail, f"审计未记录格式：{detail}"
    assert task_id in detail, f"审计未记录任务：{detail}"
    # 记录导出时的签发状态，便于追溯「当时导出的是未签发版本」
    assert "is_final" in detail, f"审计未记录签发状态：{detail}"
    _cleanup(task_id)
