# -*- coding: utf-8 -*-
"""状态机、JSON 解析、鉴权与报告渲染测试。"""
from __future__ import annotations

import datetime as dt

import pytest

from app.agent.report import overall_verdict_from_matches, render_markdown, risk_from_matches, verdict_label
from app.agent.state import InvalidTransitionError, StateMachine, TaskState
from app.constants import EvalState, Verdict
from app.core.errors import ParamInvalidError, TokenExpiredError, UnauthenticatedError
from app.core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    validate_password_strength,
    verify_password,
)
from app.llm.json_utils import extract_json, strip_code_fence


# --------------------------------------------------------------------------- #
# 状态机（SRS 4.1）
# --------------------------------------------------------------------------- #
def test_state_machine_allows_legal_transitions():
    machine = StateMachine(TaskState(task_id="t1"))
    machine.transition(EvalState.PENDING, enforce=False)
    machine.transition(EvalState.PLANNING)
    machine.transition(EvalState.RETRIEVING)
    machine.transition(EvalState.MATCHING)
    machine.transition(EvalState.ANALYZING)
    machine.transition(EvalState.REPORTING)
    machine.transition(EvalState.JUDGING)
    machine.transition(EvalState.COMPLETED)
    assert machine.state.current_state == EvalState.COMPLETED
    assert machine.state.finished_at is not None, "终态应记录结束时间"


def test_state_machine_rejects_illegal_transition():
    machine = StateMachine(TaskState(task_id="t2"))
    machine.transition(EvalState.PENDING, enforce=False)
    with pytest.raises(InvalidTransitionError) as excinfo:
        machine.transition(EvalState.COMPLETED)
    assert "COMPLETED" in str(excinfo.value)
    assert excinfo.value.details["current"] == "PENDING"


def test_state_machine_transition_table_matches_srs():
    """逐条核对 SRS 4.1 迁移表的关键边。"""
    assert StateMachine.can_transition(EvalState.RETRIEVING, EvalState.DEGRADED)
    assert StateMachine.can_transition(EvalState.JUDGING, EvalState.NEED_HUMAN)
    assert StateMachine.can_transition(EvalState.NEED_HUMAN, EvalState.MATCHING)
    assert StateMachine.can_transition(EvalState.DEGRADED, EvalState.COMPLETED)
    assert not StateMachine.can_transition(EvalState.COMPLETED, EvalState.PLANNING)
    assert not StateMachine.can_transition(EvalState.PENDING, EvalState.REPORTING)


def test_state_snapshot_contains_guard_fields():
    machine = StateMachine(TaskState(task_id="t3", thread_id="th-1"))
    machine.mark_step_done("planning", duration_ms=120, token_used=300, digest={"subtasks": 3})
    machine.add_checkpoint("ckpt-1")
    machine.record_error(error_type="llm_failed", message="timeout", retry_count=2)
    snapshot = machine.state.to_snapshot()
    assert snapshot["completed_steps"][0]["step"] == "planning"
    assert snapshot["checkpoints"] == ["ckpt-1"]
    assert snapshot["error_state"]["retry_count"] == 2
    assert snapshot["token_used"] == 300


# --------------------------------------------------------------------------- #
# JSON 容错解析（SRS 6.4 错误码 60003）
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "raw",
    [
        '{"a": 1}',
        '```json\n{"a": 1}\n```',
        '```\n{"a": 1}\n```',
        '好的，结果如下：{"a": 1} 希望有帮助',
        '{"a": 1,}',
        '{"a": 1, "b": "中文", }',
        'prefix [1, 2, 3] suffix',
    ],
)
def test_extract_json_tolerates_common_noise(raw):
    parsed = extract_json(raw)
    if isinstance(parsed, list):
        assert parsed == [1, 2, 3]
    else:
        assert parsed["a"] == 1


def test_extract_json_handles_chinese_quotes_and_nesting():
    parsed = extract_json('{"name": “地基基础”, "nested": {"x": [1, 2]}}')
    assert parsed["name"] == "地基基础"
    assert parsed["nested"]["x"] == [1, 2]


def test_extract_json_raises_on_garbage():
    with pytest.raises(ValueError):
        extract_json("这里完全没有 JSON 结构")


def test_strip_code_fence_only_removes_fence():
    assert strip_code_fence("```json\n{}\n```") == "{}"
    assert strip_code_fence("{}") == "{}"


# --------------------------------------------------------------------------- #
# 鉴权（FR-SYS-02）
# --------------------------------------------------------------------------- #
def test_password_hash_roundtrip_and_rejection():
    hashed = hash_password("Abcd1234")
    assert hashed != "Abcd1234"
    assert verify_password("Abcd1234", hashed)
    assert not verify_password("abcd1234", hashed)
    assert not verify_password("", hashed)


def test_password_longer_than_bcrypt_limit_is_handled():
    """bcrypt 仅取前 72 字节，必须显式处理而不是抛异常。"""
    long_password = "密" * 60 + "Ab1"
    hashed = hash_password(long_password)
    assert verify_password(long_password, hashed)


def test_password_strength_rules():
    validate_password_strength("Abcd1234")
    with pytest.raises(ParamInvalidError):
        validate_password_strength("short")
    with pytest.raises(ParamInvalidError):
        validate_password_strength("alllettersonly")
    with pytest.raises(ParamInvalidError):
        validate_password_strength("12345678")


def test_jwt_access_token_contains_roles():
    token = create_access_token(42, roles=["expert"])
    payload = decode_token(token, expected_type="access")
    assert payload["sub"] == "42"
    assert payload["roles"] == ["expert"]


def test_jwt_type_mismatch_is_rejected():
    refresh = create_refresh_token(42)
    with pytest.raises(UnauthenticatedError):
        decode_token(refresh, expected_type="access")


def test_jwt_expired_token_raises_dedicated_error():
    from jose import jwt

    from app.config import settings

    expired = jwt.encode(
        {
            "sub": "1",
            "type": "access",
            "exp": int((dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=5)).timestamp()),
        },
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm,
    )
    with pytest.raises(TokenExpiredError):
        decode_token(expired)


def test_jwt_tampered_token_is_rejected():
    token = create_access_token(1)
    with pytest.raises(UnauthenticatedError):
        decode_token(token[:-4] + "abcd")


# --------------------------------------------------------------------------- #
# 报告渲染（FR-AGT-07 / SRS 7.6）
# --------------------------------------------------------------------------- #
MATCHES = [
    {
        "subtask_name": "混凝土浇筑与养护过程控制",
        "clause_no": "5.3.3",
        "spec_code": "GB 50204-2015",
        "verdict": Verdict.NON_COMPLIANT.value,
        "confidence": 0.9,
        "relevance_score": 0.71,
        "evidence": "入模温度 32℃",
        "reasoning": "高于规范限值 30℃",
        "risk_level": "high",
        "remediation": "采取骨料降温措施后复测",
        "citation": {
            "chunk_id": 101,
            "clause_no": "5.3.3",
            "spec_code": "GB 50204-2015",
            "spec_name": "混凝土结构工程施工质量验收规范",
            "chapter_path": "第5章 混凝土分项工程",
            "page_no": 12,
        },
    },
    {
        "subtask_name": "原材料进场检验",
        "clause_no": "5.2.1",
        "spec_code": "GB 50204-2015",
        "verdict": Verdict.COMPLIANT.value,
        "confidence": 0.85,
        "risk_level": "low",
        "remediation": "无需整改",
        "citation": {"chunk_id": 102, "clause_no": "5.2.1", "spec_code": "GB 50204-2015"},
    },
]


def test_render_markdown_has_required_sections_and_disclaimer():
    markdown = render_markdown(
        {
            "title": "地下室剪力墙质量评估报告",
            "overview": "本次评估依据现行规范开展。",
            "overall_verdict": Verdict.NON_COMPLIANT.value,
            "risk_level": "high",
            "conclusion": "存在不符合项，需整改后重新报验。",
            "analysis": {
                "summary": "共核查 2 项，1 项不符合。",
                "findings": [
                    {
                        "clause_no": "5.3.3",
                        "problem": "入模温度超限",
                        "cause": "夏季施工未降温",
                        "risk_level": "high",
                        "remediation": "骨料降温",
                    }
                ],
            },
        },
        task_meta={"project_name": "示范项目", "specialty": "结构工程", "part": "地下室剪力墙"},
        matches=MATCHES,
        evidence_basis=[
            {"spec_code": "GB 50204-2015", "spec_name": "混凝土结构工程施工质量验收规范", "clause_no": "5.3.3", "location": "第5章 / P12"}
        ],
    )
    for section in ("一、评估概述", "二、评估依据", "三、条款逐条比对", "四、问题清单与整改建议", "五、总体结论"):
        assert section in markdown, f"报告缺少章节：{section}"
    assert "辅助生成" in markdown, "必须包含 AI 生成声明（SRS 7.6 合规要求）"
    assert "不符合" in markdown
    assert "| --- |" in markdown, "应渲染 Markdown 表格"
    assert "入模温度 32℃" in markdown, "应包含证据摘录"


def test_overall_verdict_derivation_priority():
    assert overall_verdict_from_matches([]) == Verdict.INSUFFICIENT_EVIDENCE.value
    assert (
        overall_verdict_from_matches([{"verdict": Verdict.COMPLIANT.value}, {"verdict": Verdict.NON_COMPLIANT.value}])
        == Verdict.NON_COMPLIANT.value
    )
    assert (
        overall_verdict_from_matches([{"verdict": Verdict.COMPLIANT.value}, {"verdict": Verdict.PARTIAL.value}])
        == Verdict.PARTIAL.value
    )
    assert (
        overall_verdict_from_matches([{"verdict": Verdict.COMPLIANT.value}])
        == Verdict.COMPLIANT.value
    )
    assert (
        overall_verdict_from_matches([{"verdict": Verdict.NOT_APPLICABLE.value}])
        == Verdict.NOT_APPLICABLE.value
    )


def test_risk_level_derivation_takes_worst():
    assert risk_from_matches([{"risk_level": "low"}, {"risk_level": "high"}]) == "high"
    assert risk_from_matches([{"risk_level": "low"}, {"risk_level": "medium"}]) == "medium"
    assert risk_from_matches([{"risk_level": "low"}]) == "low"
    assert risk_from_matches([]) == "low"


def test_verdict_label_maps_all_values():
    assert verdict_label(Verdict.COMPLIANT.value) == "符合"
    assert verdict_label(Verdict.NON_COMPLIANT.value) == "不符合"
    assert verdict_label(None) == "未判定"
    assert verdict_label("unknown_verdict") == "unknown_verdict"


def test_graph_store_available_does_not_deadlock():
    """回归：available 持锁后调用 _connect，而 _connect 也要取锁。

    若锁不可重入（普通 Lock）会自死锁，使启动自检与所有依赖图谱的请求永久挂起。
    这里用后台线程加超时验证属性能在有限时间内返回。
    """
    import threading

    from app.kg.graph_store import GraphStore

    store = GraphStore()
    result: list[object] = []

    def probe() -> None:
        try:
            result.append(store.available)
        except Exception as exc:  # noqa: BLE001
            result.append(exc)

    thread = threading.Thread(target=probe, daemon=True)
    thread.start()
    thread.join(timeout=45)

    assert not thread.is_alive(), "GraphStore.available 挂起（疑似自死锁）"
    assert result, "未取到结果"
    assert not isinstance(result[0], Exception), f"探测抛异常：{result[0]!r}"


def test_graph_store_available_is_non_blocking_under_concurrency():
    """并发访问时不应相互阻塞：探测锁必须是非阻塞抢占。"""
    import threading
    import time as _time

    from app.kg.graph_store import GraphStore

    store = GraphStore()
    start = _time.perf_counter()

    def probe() -> None:
        store.available

    threads = [threading.Thread(target=probe, daemon=True) for _ in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=45)

    assert all(not thread.is_alive() for thread in threads), "并发探测出现阻塞"
    # 6 个线程不应把耗时放大到串行探测的 6 倍
    assert _time.perf_counter() - start < 60, "并发探测耗时异常"


def test_graph_store_disabled_returns_false_immediately(monkeypatch):
    from app.kg import graph_store as graph_store_module

    monkeypatch.setattr(graph_store_module.settings, "neo4j_enabled", False)
    store = graph_store_module.GraphStore()
    assert store.available is False


def test_render_markdown_dedupes_model_repeated_standard_sections():
    """回归：模型常把标准章节也放进 sections，渲染后会出现重复章节。"""
    markdown = render_markdown(
        {
            "title": "重复章节回归用例",
            "overall_verdict": Verdict.NON_COMPLIANT.value,
            "risk_level": "high",
            "conclusion": "存在不符合项。",
            "analysis": {"summary": "1 项不符合", "findings": []},
            "sections": [
                {"heading": "一、评估概述", "content": "模型重复给出的概述"},
                {"heading": "二、评估依据", "content": "模型重复给出的依据"},
                {"heading": "四、问题清单与整改建议", "content": "模型重复给出的整改"},
                {"heading": "六、后续跟踪要求", "content": "这一节是渲染器没有的，应保留"},
            ],
        },
        task_meta={"part": "地下室剪力墙"},
        matches=MATCHES,
    )
    assert markdown.count("## 一、评估概述") == 1, "标准章节不应重复出现"
    assert markdown.count("## 二、评估依据") == 1
    assert "模型重复给出的概述" not in markdown, "重复章节内容应被丢弃"
    assert "六、后续跟踪要求" in markdown, "非标准章节必须保留"
    assert "这一节是渲染器没有的，应保留" in markdown


def test_render_markdown_keeps_appendix_style_custom_section():
    markdown = render_markdown(
        {
            "title": "自定义章节",
            "overall_verdict": Verdict.COMPLIANT.value,
            "analysis": {"findings": []},
            "sections": [{"heading": "附录A 检测方法说明", "content": "回弹法检测细节"}],
        },
        matches=MATCHES,
    )
    assert "附录A 检测方法说明" in markdown
    assert "回弹法检测细节" in markdown
