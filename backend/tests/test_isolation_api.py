# -*- coding: utf-8 -*-
"""接口级越权测试：真实 HTTP 请求验证项目隔离。

与 ``test_authz.py`` 的区别：那个测的是授权判定逻辑（纯函数），
这个测的是**接口是否真的接入了校验** —— 修复前这些请求会返回 200 并泄露数据。

场景：
    A 项目用户（alice）与 B 项目用户（bob），各自拥有任务与知识库。
    验证 alice 无法读取/操作 bob 的任务、报告、知识库与检索范围。
"""
from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient

from sqlalchemy import select

from app.constants import DocStatus
from app.core.security import create_access_token, hash_password
from app.db.models import EvalReport, EvalTask, KnowledgeBase, Project, SpecDoc, User


@pytest.fixture(scope="module")
def client():
    from app.main import app

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture()
def isolation_data(requires_db):
    """建立两个互相隔离的项目与用户，返回所需句柄。

    注意：不能设为 module 级 —— conftest 的 ``requires_db`` 是函数级 fixture，
    作用域不匹配会直接报 ScopeMismatch。
    每个用例重建数据，保证互不干扰。
    """
    from app.db.session import session_scope

    suffix = uuid.uuid4().hex[:8]
    with session_scope() as session:
        project_a = Project(code=f"ISO-A-{suffix}", name="隔离测试项目A")
        project_b = Project(code=f"ISO-B-{suffix}", name="隔离测试项目B")
        session.add_all([project_a, project_b])
        session.flush()

        alice = User(
            username=f"alice_{suffix}",
            full_name="A项目工程师",
            password_hash=hash_password("Test@12345"),
            role="engineer",
            project_ids=[project_a.id],
        )
        bob = User(
            username=f"bob_{suffix}",
            full_name="B项目工程师",
            password_hash=hash_password("Test@12345"),
            role="engineer",
            project_ids=[project_b.id],
        )
        session.add_all([alice, bob])
        session.flush()

        # 各项目一个知识库
        kb_a = KnowledgeBase(
            code=f"KB-A-{suffix}", name="A项目规范库", project_id=project_a.id, owner_id=alice.id
        )
        kb_b = KnowledgeBase(
            code=f"KB-B-{suffix}", name="B项目规范库", project_id=project_b.id, owner_id=bob.id
        )
        session.add_all([kb_a, kb_b])
        session.flush()

        # B 项目下的一份文档（alice 不应能看到）
        doc_b = SpecDoc(
            kb_id=kb_b.id,
            spec_code=f"GB-ISO-{suffix}",
            spec_name="B项目专属规范",
            status=DocStatus.PUBLISHED.value,
            uploader_id=bob.id,
        )
        session.add(doc_b)
        session.flush()

        # 各项目一个评估任务
        task_a = EvalTask(project_id=project_a.id, user_id=alice.id, title="A项目任务")
        task_b = EvalTask(project_id=project_b.id, user_id=bob.id, title="B项目任务")
        session.add_all([task_a, task_b])
        session.flush()

        # B 任务的报告
        report_b = EvalReport(task_id=task_b.id, markdown="# B项目报告", basis_count=1)
        session.add(report_b)
        session.flush()

        payload = {
            "alice_token": create_access_token(str(alice.id), extra={"username": alice.username}),
            "bob_token": create_access_token(str(bob.id), extra={"username": bob.username}),
            "task_a": str(task_a.id),
            "task_b": str(task_b.id),
            "report_b": str(report_b.id),
            "kb_a": kb_a.id,
            "kb_b": kb_b.id,
            "doc_b": doc_b.id,
            "project_a": project_a.id,
            "project_b": project_b.id,
        }

    yield payload

    # 清理：必须按外键依赖「从叶到根」删除，否则会 ForeignKeyViolation。
    # 依赖链：judge_score/judge_review -> eval_report -> eval_task -> {subtask,match,step}
    #         doc_chunk -> doc_version -> spec_doc -> knowledge_base -> {project, sys_user}
    from sqlalchemy import delete

    from app.db.models import (
        AgentStepLog,
        DocChunk,
        DocVersion,
        EvalSubtask,
        JudgeReview,
        JudgeScore,
        MatchResult,
    )

    with session_scope() as session:
        task_ids = [uuid.UUID(payload["task_a"]), uuid.UUID(payload["task_b"])]
        report_ids = [uuid.UUID(payload["report_b"])]

        session.execute(delete(JudgeScore).where(JudgeScore.report_id.in_(report_ids)))
        session.execute(delete(JudgeReview).where(JudgeReview.report_id.in_(report_ids)))
        session.execute(delete(EvalReport).where(EvalReport.task_id.in_(task_ids)))
        session.execute(delete(EvalSubtask).where(EvalSubtask.task_id.in_(task_ids)))
        session.execute(delete(MatchResult).where(MatchResult.task_id.in_(task_ids)))
        session.execute(delete(AgentStepLog).where(AgentStepLog.task_id.in_(task_ids)))
        session.execute(delete(EvalTask).where(EvalTask.id.in_(task_ids)))

        doc_versions = session.execute(
            select(DocVersion.id).where(DocVersion.doc_id == payload["doc_b"])
        ).scalars().all()
        if doc_versions:
            session.execute(delete(DocChunk).where(DocChunk.version_id.in_(doc_versions)))
            session.execute(delete(DocVersion).where(DocVersion.id.in_(doc_versions)))
        session.execute(delete(SpecDoc).where(SpecDoc.id == payload["doc_b"]))
        session.execute(
            delete(KnowledgeBase).where(
                KnowledgeBase.id.in_([payload["kb_a"], payload["kb_b"]])
            )
        )
        session.execute(
            delete(User).where(User.username.in_([f"alice_{suffix}", f"bob_{suffix}"]))
        )
        session.execute(
            delete(Project).where(
                Project.id.in_([payload["project_a"], payload["project_b"]])
            )
        )


def _headers(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# --------------------------------------------------------------------------- #
# 任务隔离
# --------------------------------------------------------------------------- #
def test_task_list_only_returns_own_project(client, isolation_data):
    """任务列表默认只返回可见范围（修复前默认返回全部用户的任务）。"""
    response = client.get("/api/v1/eval/tasks?page_size=100", headers=_headers(isolation_data["alice_token"]))
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["data"]["items"]}

    assert isolation_data["task_a"] in ids, "应能看到自己项目的任务"
    assert isolation_data["task_b"] not in ids, "不应看到其他项目的任务"


def test_get_other_project_task_is_forbidden(client, isolation_data):
    """按 ID 访问他人任务必须 403（修复前只判断存在，会泄露任务详情）。"""
    response = client.get(
        f"/api/v1/eval/tasks/{isolation_data['task_b']}",
        headers=_headers(isolation_data["alice_token"]),
    )
    assert response.status_code in (403, 404), f"期望被拒绝，实际 {response.status_code}"


def test_own_task_is_accessible(client, isolation_data):
    """自己的任务仍应正常可读（防止修复过度）。"""
    response = client.get(
        f"/api/v1/eval/tasks/{isolation_data['task_a']}",
        headers=_headers(isolation_data["alice_token"]),
    )
    assert response.status_code == 200


def test_run_other_project_task_is_forbidden(client, isolation_data):
    """不能重跑他人任务（修复前会真的执行并产生副作用）。"""
    response = client.post(
        f"/api/v1/eval/tasks/{isolation_data['task_b']}/run",
        headers=_headers(isolation_data["alice_token"]),
    )
    assert response.status_code in (403, 404), f"期望被拒绝，实际 {response.status_code}"


# --------------------------------------------------------------------------- #
# 报告隔离
# --------------------------------------------------------------------------- #
def test_read_other_project_report_is_forbidden(client, isolation_data):
    """读取他人报告必须被拒（修复前会直接返回报告内容）。"""
    response = client.get(
        f"/api/v1/eval/tasks/{isolation_data['task_b']}/report",
        headers=_headers(isolation_data["alice_token"]),
    )
    assert response.status_code in (403, 404), f"期望被拒绝，实际 {response.status_code}"


def test_list_other_project_report_reviews_is_forbidden(client, isolation_data):
    """评审列表同样受任务归属保护。"""
    response = client.get(
        f"/api/v1/judge/reports/{isolation_data['report_b']}/reviews",
        headers=_headers(isolation_data["alice_token"]),
    )
    assert response.status_code in (403, 404), f"期望被拒绝，实际 {response.status_code}"


# --------------------------------------------------------------------------- #
# 知识库隔离
# --------------------------------------------------------------------------- #
def test_kb_list_excludes_other_project(client, isolation_data):
    """知识库列表不应包含他人项目的库。"""
    response = client.get("/api/v1/kb", headers=_headers(isolation_data["alice_token"]))
    assert response.status_code == 200
    ids = {item["id"] for item in response.json()["data"]}

    assert isolation_data["kb_a"] in ids, "应能看到自己项目的知识库"
    assert isolation_data["kb_b"] not in ids, "不应看到其他项目的知识库"


def test_read_other_project_document_is_forbidden(client, isolation_data):
    """读取他人项目知识库下的文档必须被拒。"""
    response = client.get(
        f"/api/v1/kb/documents/{isolation_data['doc_b']}",
        headers=_headers(isolation_data["alice_token"]),
    )
    assert response.status_code in (403, 404), f"期望被拒绝，实际 {response.status_code}"


# --------------------------------------------------------------------------- #
# 检索隔离（修复前传参即可查任意库）
# --------------------------------------------------------------------------- #
def test_search_other_project_kb_is_forbidden(client, isolation_data):
    """显式指定他人知识库检索必须 403。"""
    response = client.post(
        "/api/v1/retrieval/search",
        headers=_headers(isolation_data["alice_token"]),
        json={"query": "混凝土入模温度", "kb_ids": [isolation_data["kb_b"]], "top_k": 3},
    )
    assert response.status_code == 403, f"期望 403，实际 {response.status_code}"


def test_search_other_project_namespace_is_forbidden(client, isolation_data):
    """namespace 决定加载哪个索引分片，越权同样必须阻断。"""
    response = client.post(
        f"/api/v1/retrieval/search?namespace=kb_{isolation_data['kb_b']}",
        headers=_headers(isolation_data["alice_token"]),
        json={"query": "混凝土入模温度", "top_k": 3},
    )
    assert response.status_code == 403, f"期望 403，实际 {response.status_code}"


def test_chat_other_project_kb_is_forbidden(client, isolation_data):
    """问答接口与检索接口共用同一授权校验。"""
    response = client.post(
        "/api/v1/retrieval/chat",
        headers=_headers(isolation_data["alice_token"]),
        json={"query": "混凝土入模温度", "kb_ids": [isolation_data["kb_b"]]},
    )
    assert response.status_code == 403, f"期望 403，实际 {response.status_code}"


def test_search_own_kb_is_allowed(client, isolation_data):
    """授权范围内的检索必须正常放行（防止修复过度）。"""
    response = client.post(
        "/api/v1/retrieval/search",
        headers=_headers(isolation_data["alice_token"]),
        json={"query": "混凝土入模温度", "kb_ids": [isolation_data["kb_a"]], "top_k": 3},
    )
    assert response.status_code == 200, f"自己的知识库应可检索，实际 {response.status_code}"
