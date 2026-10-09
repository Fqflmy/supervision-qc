# -*- coding: utf-8 -*-
"""状态与标签一致性回归（由一次真实的界面异常挖出的三处缺陷）。

现象：质量评审页显示「待执行 + 待复核」，而库里是
``NEED_HUMAN`` + 已签发。顺藤摸出三个问题：

1. **``overall_verdict`` 有两套词汇**，只按一套解析会 500。
   报告在有 Judge 总分时存**等级**（``qualified``/``excellent``），
   仅按条款汇总时存**条款判定**（``non_compliant``/``partial``）。
   这个坑出现过两次（先用 Verdict 解析等级值，改 JudgeGrade 后又解析不了条款值），
   所以必须有测试同时覆盖两套取值。

2. **重跑未清除人工裁定**：``_upsert_report`` 只更新报告内容，
   于是新报告继承了上一版的 ``human_verdict``/``is_final`` ——
   等于一份**从未被人看过**的新报告凭空带上了签认。

3. **已签发报告可被随意重跑**，一次误点就作废人工复核成果，且无提示。
"""
from __future__ import annotations

import uuid

import pytest


# --------------------------------------------------------------------------- #
# 1) 标签解析必须兼容两套词汇
# --------------------------------------------------------------------------- #
def test_overall_verdict_label_accepts_both_vocabularies():
    """等级值与条款判定值都必须能解析，且未知值不得抛异常。"""
    from app.constants import overall_verdict_label

    # 等级词汇（JudgeGrade）
    assert overall_verdict_label("excellent") == "优秀"
    assert overall_verdict_label("good") == "良好"
    assert overall_verdict_label("qualified") == "合格"
    assert overall_verdict_label("unqualified") == "不合格"

    # 条款判定词汇（Verdict）—— 这是导致 500 的那一类
    assert overall_verdict_label("non_compliant") == "不符合"
    assert overall_verdict_label("compliant") == "符合"
    assert overall_verdict_label("partial") == "部分符合"
    assert overall_verdict_label("insufficient_evidence") == "证据不足"
    assert overall_verdict_label("not_applicable") == "不适用"

    # 空值与未知值不得抛异常（展示层不该因多一个枚举值就 500）
    assert overall_verdict_label(None) is None
    assert overall_verdict_label("") is None
    assert overall_verdict_label("some_future_value") == "some_future_value"


def test_review_status_endpoint_survives_clause_vocabulary(client, requires_db):
    """报告的 overall_verdict 是条款判定时，复核状态接口不得 500。"""
    project_id, task_id = _seed_task_with_report("NEED_HUMAN", overall_verdict="non_compliant")
    _ensure_user("lbl_expert", "expert", project_id)
    headers = _login(client, "lbl_expert")

    response = client.get(f"/api/v1/eval/tasks/{task_id}/review", headers=headers)
    assert response.status_code == 200, (
        f"条款判定词汇导致接口失败：{response.status_code} {response.text[:250]}"
    )
    data = response.json()["data"]
    assert data["machine_verdict"] == "non_compliant"
    assert data["machine_verdict_label"] == "不符合"


def test_review_status_endpoint_survives_grade_vocabulary(client, requires_db):
    """报告的 overall_verdict 是等级时同样不得失败（两套都要能过）。"""
    project_id, task_id = _seed_task_with_report("NEED_HUMAN", overall_verdict="qualified")
    _ensure_user("lbl_expert", "expert", project_id)
    headers = _login(client, "lbl_expert")

    response = client.get(f"/api/v1/eval/tasks/{task_id}/review", headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["data"]["machine_verdict_label"] == "合格"


def test_task_list_returns_version(client, requires_db):
    """列表必须返回 version —— 队列内裁定需要它做乐观锁，缺了就无法防并发。"""
    project_id, task_id = _seed_task_with_report("NEED_HUMAN")
    _ensure_user("lbl_expert", "expert", project_id)
    headers = _login(client, "lbl_expert")

    response = client.get("/api/v1/eval/tasks?page_size=100", headers=headers)
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    target = next((i for i in items if i["id"] == task_id), None)
    assert target is not None, "任务未出现在列表中"
    assert target.get("version") is not None, "列表未返回 version"


# --------------------------------------------------------------------------- #
# 2) 重跑必须清除上一次人工裁定
# --------------------------------------------------------------------------- #
def test_rerun_clears_previous_human_verdict(client, requires_db):
    """重跑生成新报告后，不得继承上一版的签认。

    继承会造成矛盾数据：任务待复核，报告却已签发 ——
    等于一份从未被人看过的新报告凭空带上了签认。
    """
    from app.agent.runner import _persist_report
    from app.db.models import EvalTask
    from app.db.session import session_scope

    project_id, task_id = _seed_task_with_report("NEED_HUMAN")
    _ensure_user("lbl_expert", "expert", project_id)
    headers = _login(client, "lbl_expert")

    # 先签发
    response = client.post(
        f"/api/v1/eval/tasks/{task_id}/review",
        headers=headers,
        json={"verdict": "qualified", "comment": "先签发"},
    )
    assert response.status_code == 200, response.text

    # 直接用报告写入函数模拟「重跑后重新生成报告」
    with session_scope() as session:
        task = session.get(EvalTask, uuid.UUID(task_id))
        assert task.report.is_final is True, "前置条件不成立：应已签发"
        _persist_report(
            session,
            task_id=task.id,
            report={"conclusion": "重跑后的新结论", "overall_verdict": "non_compliant"},
            markdown="# 重跑",
            matches=[],
            generator_model="deepseek-chat",
        )
        session.flush()
        report = task.report
        assert report.human_verdict is None, (
            "重跑后仍带着上一版的人工裁定 —— 新报告凭空继承签认"
        )
        assert report.is_final is False, "重跑后仍标记为已签发"
        assert report.reviewed_by is None
        assert report.reviewed_at is None
        assert report.review_comment is None
        # 报告内容本身应已更新
        assert report.conclusion == "重跑后的新结论"


# --------------------------------------------------------------------------- #
# 3) 已签发报告重跑需显式确认
# --------------------------------------------------------------------------- #
def _run_request(client, headers, task_id: str, **params):
    from urllib.parse import urlencode

    query = f"?{urlencode(params)}" if params else ""
    return client.post(f"/api/v1/eval/tasks/{task_id}/run{query}", headers=headers)


def test_signed_report_rerun_requires_force(client, requires_db):
    """已签发报告重跑必须带 force=true，否则拒绝 —— 防误点作废人工复核。"""
    project_id, task_id = _seed_task_with_report("NEED_HUMAN")
    _ensure_user("lbl_expert", "expert", project_id)
    _ensure_user("lbl_engineer", "engineer", project_id)

    expert_headers = _login(client, "lbl_expert")
    engineer_headers = _login(client, "lbl_engineer")

    signed = client.post(
        f"/api/v1/eval/tasks/{task_id}/review",
        headers=expert_headers,
        json={"verdict": "qualified", "comment": "签发"},
    )
    assert signed.status_code == 200, signed.text

    # 不带 force：必须拒绝且给出可操作的提示
    blocked = _run_request(client, engineer_headers, task_id, resume="true")
    assert blocked.status_code == 409, (
        f"已签发报告未加保护即可重跑：{blocked.status_code}"
    )
    assert "force" in blocked.text, f"提示未告知如何确认重跑：{blocked.text[:200]}"

    # 带 force：放行到业务层（不再是 409 保护性拒绝）
    allowed = _run_request(client, engineer_headers, task_id, resume="true", force="true")
    assert allowed.status_code != 409 or "force" not in allowed.text, (
        f"force=true 仍被保护性拒绝：{allowed.status_code} {allowed.text[:200]}"
    )


def test_unsigned_report_rerun_not_blocked(client, requires_db):
    """未签发的报告不受该防护影响（早期版本全都被拦会破坏正常流程）。"""
    project_id, task_id = _seed_task_with_report("NEED_HUMAN")
    _ensure_user("lbl_engineer", "engineer", project_id)
    headers = _login(client, "lbl_engineer")

    response = _run_request(client, headers, task_id, resume="true")
    assert "已由人工复核签发" not in response.text, (
        "未签发的报告被误拦为「已签发」"
    )


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
                    full_name=f"一致性测试-{role}",
                    password_hash=hash_password("Admin@12345"),
                    role=role,
                    project_ids=[project_id] if project_id else [],
                )
            )
        elif project_id and project_id not in (row.project_ids or []):
            row.project_ids = sorted({*(row.project_ids or []), project_id})


def _seed_task_with_report(state: str = "NEED_HUMAN", *, overall_verdict: str = "qualified"):
    from app.db.models import EvalReport, EvalTask, Project, User
    from app.db.session import session_scope

    with session_scope() as session:
        project = session.query(Project).order_by(Project.id).first()
        owner = session.query(User).filter(User.username == "engineer").one_or_none()
        if owner is None:
            raise RuntimeError("缺少 engineer 账号")

        task = EvalTask(
            id=uuid.uuid4(),
            project_id=project.id,
            user_id=owner.id,
            title="状态一致性测试任务",
            eval_type="inspection_lot",
            specialty="结构工程",
            current_state=state,
            iteration_count=3,
            version=1,
        )
        session.add(task)
        session.flush()
        session.add(
            EvalReport(
                id=uuid.uuid4(),
                task_id=task.id,
                conclusion="一致性测试报告",
                overall_verdict=overall_verdict,
                risk_level="medium",
                summary="测试",
                content={"matches": []},
                basis_count=2,
                non_compliance_count=1,
                generator_model="deepseek-chat",
                human_verdict=None,
                is_final=False,
            )
        )
        return project.id, str(task.id)
