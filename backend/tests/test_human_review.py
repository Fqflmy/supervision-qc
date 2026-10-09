# -*- coding: utf-8 -*-
"""人工复核裁定与终审签发（FR-AGT-11 / FR-JDG-05 / FR-JDG-06）。

覆盖业务规则而非仅接口连通：

1. **裁定合格 → 签发生效**：`is_final=True`、任务 COMPLETED、可作正式依据；
2. **裁定不合格 → 不签发**：`is_final=False`；`rerun` 决定驳回重跑或直接落定；
3. **机器结论不被覆盖**：人工裁定后 `overall_verdict` 保持原值（可对比、可作样本）；
4. **判定不合格必须填依据**：缺 comment 返回 422/400；
5. **perdict 枚举校验**：非法取值被拒（不再是自由字符串）；
6. **权限**：只读/工程师不能裁定；审核人员与管理员可以；
7. **乐观锁**：`expected_version` 不匹配返回 409；
8. **留痕**：每次裁定写 human_feedback（含分歧标记）+ audit_log；
9. **待复核队列口径**：只返回尚无人工裁定的任务。
"""
from __future__ import annotations

import uuid

import pytest


# --------------------------------------------------------------------------- #
# 夹具
# --------------------------------------------------------------------------- #
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


def _ensure_user(username: str, role: str, project_id: int | None = None) -> None:
    from app.core.security import hash_password
    from app.db.models import User
    from app.db.session import session_scope

    with session_scope() as session:
        row = session.query(User).filter(User.username == username).one_or_none()
        if row is None:
            session.add(
                User(
                    username=username,
                    full_name=f"复核测试-{role}",
                    password_hash=hash_password("Admin@12345"),
                    role=role,
                    project_ids=[project_id] if project_id else [],
                )
            )
        elif project_id and project_id not in (row.project_ids or []):
            row.project_ids = sorted({*(row.project_ids or []), project_id})


def _seed_task(state: str = "NEED_HUMAN", *, with_report: bool = True) -> tuple[int, str]:
    """造一个「有报告 + 指定状态」的任务，返回 (project_id, task_id)。"""
    from app.db.models import EvalReport, EvalTask, Project, User
    from app.db.session import session_scope

    with session_scope() as session:
        project = session.query(Project).order_by(Project.id).first()
        if project is None:
            project = Project(code="REV-T1", name="复核测试项目", specialty="结构工程")
            session.add(project)
            session.flush()

        owner = session.query(User).filter(User.username == "engineer").one_or_none()
        if owner is None:
            raise RuntimeError("缺少 engineer 账号")

        task = EvalTask(
            id=uuid.uuid4(),
            project_id=project.id,
            user_id=owner.id,
            title="人工复核测试任务",
            eval_type="inspection_lot",
            specialty="结构工程",
            current_state=state,
            iteration_count=2,
            version=1,
        )
        session.add(task)
        session.flush()

        if with_report:
            session.add(
                EvalReport(
                    id=uuid.uuid4(),
                    task_id=task.id,
                    conclusion="混凝土强度满足设计要求",
                    overall_verdict="qualified",
                    risk_level="medium",
                    summary="测试报告",
                    content={"matches": []},
                    basis_count=5,
                    non_compliance_count=1,
                    generator_model="deepseek-chat",
                    # 关键：初始未裁定、未签发
                    human_verdict=None,
                    is_final=False,
                )
            )
        return project.id, str(task.id)


def _report_of(task_id: str):
    from sqlalchemy import select

    from app.db.models import EvalReport
    from app.db.session import session_scope

    with session_scope() as session:
        return session.execute(
            select(EvalReport).where(EvalReport.task_id == uuid.UUID(task_id))
        ).scalar_one_or_none()


def _task_of(task_id: str):
    from app.db.models import EvalTask
    from app.db.session import session_scope

    with session_scope() as session:
        return session.get(EvalTask, uuid.UUID(task_id))


# --------------------------------------------------------------------------- #
# 1) 判定合格 → 签发
# --------------------------------------------------------------------------- #
def test_qualified_signs_report(client, requires_db):
    project_id, task_id = _seed_task("NEED_HUMAN")
    _ensure_user("rev_expert", "expert", project_id)
    headers = _login(client, "rev_expert")

    response = client.post(
        f"/api/v1/eval/tasks/{task_id}/review",
        headers=headers,
        json={"verdict": "qualified", "comment": "已核对引用条款，同意 AI 结论"},
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["human_verdict"] == "qualified"
    assert data["is_final"] is True, "判定合格必须签发生效"
    assert data["current_state"] == "COMPLETED"

    report = _report_of(task_id)
    assert report.is_final is True
    assert report.reviewed_by is not None, "必须记录签认人"
    assert report.reviewed_at is not None, "必须记录签认时间"
    assert report.review_comment


def test_qualified_preserves_machine_verdict(client, requires_db):
    """**关键**：人工裁定不得覆盖机器结论。"""
    project_id, task_id = _seed_task("NEED_HUMAN")
    _ensure_user("rev_expert", "expert", project_id)
    headers = _login(client, "rev_expert")

    before = _report_of(task_id).overall_verdict

    client.post(
        f"/api/v1/eval/tasks/{task_id}/review",
        headers=headers,
        json={"verdict": "qualified", "comment": "同意"},
    )

    after = _report_of(task_id)
    assert after.overall_verdict == before, "机器结论被人工覆盖 —— 违反设计原则"
    assert after.human_verdict == "qualified"
    # 两者并存，可对比
    assert after.overall_verdict != after.human_verdict or before == "qualified"


# --------------------------------------------------------------------------- #
# 2) 判定不合格
# --------------------------------------------------------------------------- #
def test_unqualified_with_rerun_goes_back_to_matching(client, requires_db):
    project_id, task_id = _seed_task("NEED_HUMAN")
    _ensure_user("rev_expert", "expert", project_id)
    headers = _login(client, "rev_expert")

    response = client.post(
        f"/api/v1/eval/tasks/{task_id}/review",
        headers=headers,
        json={
            "verdict": "unqualified",
            "comment": "第 3 条引用已废止版本，且缺少材料复验记录",
            "rerun": True,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["human_verdict"] == "unqualified"
    assert data["is_final"] is False, "不合格不得签发"
    assert data["rerun"] is True
    assert data["current_state"] == "MATCHING", "驳回重跑应回到条款匹配阶段"


def test_unqualified_without_rerun_finalizes_as_rejected(client, requires_db):
    """直接落定不合格：任务终结，但报告仍不是正式件。"""
    project_id, task_id = _seed_task("NEED_HUMAN")
    _ensure_user("rev_expert", "expert", project_id)
    headers = _login(client, "rev_expert")

    response = client.post(
        f"/api/v1/eval/tasks/{task_id}/review",
        headers=headers,
        json={
            "verdict": "unqualified",
            "comment": "材料确实不足，重跑无意义",
            "rerun": False,
        },
    )
    assert response.status_code == 200, response.text
    data = response.json()["data"]

    assert data["is_final"] is False
    assert data["rerun"] is False
    assert data["current_state"] == "COMPLETED"

    report = _report_of(task_id)
    assert report.is_final is False, "不合格的报告绝不能成为正式件"


def test_unqualified_requires_comment(client, requires_db):
    """判定不合格必须说明依据 —— 质量责任的基本要求。"""
    project_id, task_id = _seed_task("NEED_HUMAN")
    _ensure_user("rev_expert", "expert", project_id)
    headers = _login(client, "rev_expert")

    for empty in ["", "   "]:
        response = client.post(
            f"/api/v1/eval/tasks/{task_id}/review",
            headers=headers,
            json={"verdict": "unqualified", "comment": empty, "rerun": False},
        )
        assert response.status_code in (400, 422), (
            f"判定不合格未填依据却被接受：{response.status_code}"
        )


# --------------------------------------------------------------------------- #
# 3) 枚举校验
# --------------------------------------------------------------------------- #
def test_invalid_verdict_rejected(client, requires_db):
    """verdict 必须是枚举值，不再是自由字符串。"""
    project_id, task_id = _seed_task("NEED_HUMAN")
    _ensure_user("rev_expert", "expert", project_id)
    headers = _login(client, "rev_expert")

    for bad in ["pass", "合格", "ok", "confirmed", ""]:
        response = client.post(
            f"/api/v1/eval/tasks/{task_id}/review",
            headers=headers,
            json={"verdict": bad, "comment": "x"},
        )
        assert response.status_code == 422, f"非法 verdict {bad!r} 未被拒绝"


# --------------------------------------------------------------------------- #
# 4) 权限
# --------------------------------------------------------------------------- #
def test_only_review_roles_can_decide(client, requires_db):
    project_id, task_id = _seed_task("NEED_HUMAN")
    for username, role in [("rev_viewer", "viewer"), ("rev_engineer", "engineer")]:
        _ensure_user(username, role, project_id)

    for username in ["rev_viewer", "rev_engineer"]:
        headers = _login(client, username)
        response = client.post(
            f"/api/v1/eval/tasks/{task_id}/review",
            headers=headers,
            json={"verdict": "qualified", "comment": "越权尝试"},
        )
        assert response.status_code == 403, (
            f"{username}（无 eval:review）竟能裁定：{response.status_code}"
        )


def test_admin_can_decide(client, requires_db):
    _, task_id = _seed_task("NEED_HUMAN")
    headers = _login(client, "admin")
    response = client.post(
        f"/api/v1/eval/tasks/{task_id}/review",
        headers=headers,
        json={"verdict": "qualified", "comment": "管理员复核"},
    )
    assert response.status_code == 200, response.text


# --------------------------------------------------------------------------- #
# 5) 乐观锁与改判
# --------------------------------------------------------------------------- #
def test_stale_version_conflicts(client, requires_db):
    project_id, task_id = _seed_task("NEED_HUMAN")
    _ensure_user("rev_expert", "expert", project_id)
    headers = _login(client, "rev_expert")

    response = client.post(
        f"/api/v1/eval/tasks/{task_id}/review",
        headers=headers,
        json={"verdict": "qualified", "comment": "第一次", "expected_version": 999},
    )
    assert response.status_code == 409, "过期版本未触发冲突"


def test_reversal_allowed_on_completed(client, requires_db):
    """已签发的报告可改判（保留历次记录）—— 监理常有「复核后发现遗漏」。"""
    project_id, task_id = _seed_task("NEED_HUMAN")
    _ensure_user("rev_expert", "expert", project_id)
    headers = _login(client, "rev_expert")

    first = client.post(
        f"/api/v1/eval/tasks/{task_id}/review",
        headers=headers,
        json={"verdict": "qualified", "comment": "初判合格"},
    )
    assert first.status_code == 200
    assert _report_of(task_id).is_final is True

    # 改判为不合格（不重跑）
    version = first.json()["data"]["version"]
    second = client.post(
        f"/api/v1/eval/tasks/{task_id}/review",
        headers=headers,
        json={
            "verdict": "unqualified",
            "comment": "复核后发现遗漏，撤回签发",
            "rerun": False,
            "expected_version": version,
        },
    )
    assert second.status_code == 200, (
        f"已签发报告无法改判：{second.status_code} {second.text[:200]}"
    )
    report = _report_of(task_id)
    assert report.human_verdict == "unqualified"
    assert report.is_final is False, "改判不合格后必须撤销签发"

    # 历次记录都在（2 条整体裁定）
    from sqlalchemy import select

    from app.db.models import HumanFeedback
    from app.db.session import session_scope

    with session_scope() as session:
        rows = (
            session.execute(
                select(HumanFeedback).where(HumanFeedback.task_id == uuid.UUID(task_id))
            )
            .scalars()
            .all()
        )
        verdicts = [r.action for r in rows]
    assert "qualified" in verdicts and "unqualified" in verdicts, (
        f"历次裁定记录丢失：{verdicts}"
    )


# --------------------------------------------------------------------------- #
# 6) 留痕与审计
# --------------------------------------------------------------------------- #
def test_decision_writes_audit_and_divergence_flag(client, requires_db):
    """人工判不合格、机器判合格 → 应标记为分歧（FR-JDG-06 样本）。"""
    from sqlalchemy import select

    from app.db.models import AuditLog
    from app.db.session import session_scope

    project_id, task_id = _seed_task("NEED_HUMAN")
    _ensure_user("rev_expert", "expert", project_id)
    headers = _login(client, "rev_expert")

    client.post(
        f"/api/v1/eval/tasks/{task_id}/review",
        headers=headers,
        json={"verdict": "unqualified", "comment": "与 AI 结论分歧", "rerun": False},
    )

    with session_scope() as session:
        logs = (
            session.execute(
                select(AuditLog).where(AuditLog.action == "report_review")
            )
            .scalars()
            .all()
        )
    assert logs, "裁定未写入审计日志"

    detail = (logs[-1].detail or {}) if hasattr(logs[-1], "detail") else {}
    assert detail.get("diverged") is True, (
        f"人工不合格 + 机器合格 未标记为分歧：{detail}"
    )
    assert detail.get("machine_verdict") == "qualified", "审计未保留机器结论"


# --------------------------------------------------------------------------- #
# 7) 状态与查询
# --------------------------------------------------------------------------- #
def test_cannot_review_task_in_progress(client, requires_db):
    project_id, task_id = _seed_task("PLANNING")
    _ensure_user("rev_expert", "expert", project_id)
    headers = _login(client, "rev_expert")

    response = client.post(
        f"/api/v1/eval/tasks/{task_id}/review",
        headers=headers,
        json={"verdict": "qualified", "comment": "执行中不应可裁定"},
    )
    assert response.status_code == 409, (
        f"执行中的任务竟可裁定：{response.status_code}"
    )


def test_review_status_endpoint(client, requires_db):
    project_id, task_id = _seed_task("NEED_HUMAN")
    _ensure_user("rev_expert", "expert", project_id)
    headers = _login(client, "rev_expert")

    before = client.get(f"/api/v1/eval/tasks/{task_id}/review", headers=headers)
    assert before.status_code == 200, before.text
    data = before.json()["data"]
    assert data["review_status"] == "pending"
    assert data["human_verdict"] is None
    assert data["is_final"] is False
    assert data["machine_verdict"] == "qualified", "应同时返回机器结论"

    client.post(
        f"/api/v1/eval/tasks/{task_id}/review",
        headers=headers,
        json={"verdict": "qualified", "comment": "同意"},
    )

    after = client.get(f"/api/v1/eval/tasks/{task_id}/review", headers=headers).json()["data"]
    assert after["review_status"] == "signed"
    assert after["human_verdict_label"] == "合格"
    assert after["reviewed_by_name"], "应返回签认人姓名"


def test_pending_queue_excludes_decided(client, requires_db):
    """待复核队列按「尚无人工裁定」判定，裁定后应移出队列。"""
    project_id, task_id = _seed_task("NEED_HUMAN")
    _ensure_user("rev_expert", "expert", project_id)
    headers = _login(client, "rev_expert")

    before = client.get("/api/v1/eval/reviews/pending?page_size=100", headers=headers)
    assert before.status_code == 200, before.text
    ids_before = [item["task_id"] for item in before.json()["data"]["items"]]
    assert task_id in ids_before, "待复核任务未出现在队列中"

    client.post(
        f"/api/v1/eval/tasks/{task_id}/review",
        headers=headers,
        json={"verdict": "qualified", "comment": "处理完毕"},
    )

    after = client.get("/api/v1/eval/reviews/pending?page_size=100", headers=headers)
    ids_after = [item["task_id"] for item in after.json()["data"]["items"]]
    assert task_id not in ids_after, "已裁定的任务仍在待复核队列中"


def test_pending_queue_requires_review_permission(client, requires_db):
    project_id, _ = _seed_task("NEED_HUMAN")
    _ensure_user("rev_viewer", "viewer", project_id)
    headers = _login(client, "rev_viewer")

    response = client.get("/api/v1/eval/reviews/pending", headers=headers)
    assert response.status_code == 403


# --------------------------------------------------------------------------- #
# 8) 收敛规则：负面结论必须转人工
# --------------------------------------------------------------------------- #
def test_non_compliant_report_requires_human():
    """结论「不符合」必须转人工复核。

    缺陷背景：收敛逻辑此前只按「证据不足」转人工，「不符合」不触发 ——
    于是当**未执行 LLM-as-Judge**（没有分数、低分闸门无从触发）时，
    一份「不符合 + 高风险」的报告直接到达 COMPLETED，等同正式出具。
    """
    from app.agent.graph import _report_needs_human

    assert _report_needs_human({"report": {"overall_verdict": "non_compliant"}}) is True
    assert _report_needs_human(
        {"report": {"overall_verdict": "qualified", "risk_level": "high"}}
    ) is True


def test_ordinary_report_does_not_force_human():
    """普通结论不强制转人工 —— 否则绝大多数任务都会转人工，失去拦截意义。"""
    from app.agent.graph import _report_needs_human

    # 「部分符合 + 中风险」是常见中间结论，不应强制复核
    assert (
        _report_needs_human({"report": {"overall_verdict": "partial", "risk_level": "medium"}})
        is False
    )
    assert (
        _report_needs_human({"report": {"overall_verdict": "qualified", "risk_level": "low"}})
        is False
    )


def test_report_needs_human_handles_missing_data():
    """数据缺失时不得抛异常（收敛节点在异常路径上也会被调用）。"""
    from app.agent.graph import _report_needs_human

    for state in ({}, {"report": None}, {"report": {}}, {"report": "not-a-dict"}):
        assert _report_needs_human(state) is False, f"输入 {state!r} 未安全处理"
