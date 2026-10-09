# -*- coding: utf-8 -*-
"""Agent 工具调用权限的回归测试（对应权限设计的「工具调用权限」）。

覆盖三项目标：
1. **工具级授权** —— 缺少权限的角色调用工具被拒绝，且不产生副作用；
2. **参数校验** —— 非法/超限参数被拒绝（防止越权查询与资源耗尽）；
3. **调用审计** —— 每次调用都产生一条记录，含是否放行与拒绝原因。

另外覆盖最关键的越权场景：**工具内部的检索范围必须落在调用者授权集合内**，
即使任务里存着越权的 ``kb_ids`` 也不能放行。
"""
from __future__ import annotations

import pytest

from app.agent.tools import (
    MAX_QUERY_CHARS,
    MAX_TOP_K,
    ToolSpec,
    get_registry,
    reset_registry,
    validate_kb_retrieve,
)


@pytest.fixture(autouse=True)
def _fresh_registry():
    reset_registry()
    yield
    reset_registry()


class FakeUser:
    def __init__(self, uid=1, role="engineer", project_ids=None):
        self.id = uid
        self.role = role
        self.project_ids = project_ids if project_ids is not None else []


class FakeClause:
    def __init__(self, chunk_id=1):
        self._id = chunk_id

    def to_dict(self):
        return {"chunk_id": self._id, "clause_no": "5.3.3", "content": "混凝土强度"}


class FakeResult:
    def __init__(self, clauses=None, no_evidence=False):
        self.clauses = clauses if clauses is not None else [FakeClause()]
        self.no_evidence = no_evidence
        self.latency_ms = 12
        self.sub_queries = ["混凝土强度"]


class FakePipeline:
    def __init__(self, result=None, raises=None):
        self._result = result or FakeResult()
        self._raises = raises
        self.calls: list[dict] = []

    async def retrieve(self, query, session, **kwargs):
        self.calls.append({"query": query, **kwargs})
        if self._raises:
            raise self._raises
        return self._result


class FakeSession:
    def __init__(self, allowed_ids=None):
        self._allowed = allowed_ids if allowed_ids is not None else [1, 2]

    def execute(self, _stmt):
        class _R:
            def __init__(self, values):
                self._values = values

            def scalars(self):
                return self

            def all(self):
                return list(self._values)

        return _R(self._allowed)


class FakeCtx:
    def __init__(self, user=None, allowed_kb_ids=None, pipeline=None):
        self.user = user or FakeUser()
        self.session = FakeSession(allowed_kb_ids)
        self.pipeline = pipeline or FakePipeline()
        self.tool_calls: list = []


# --------------------------------------------------------------------------- #
# 参数校验
# --------------------------------------------------------------------------- #
def test_validator_rejects_missing_query():
    assert "query" in (validate_kb_retrieve({}) or "")
    assert "query" in (validate_kb_retrieve({"query": "   "}) or "")


def test_validator_rejects_overlong_query():
    assert validate_kb_retrieve({"query": "x" * (MAX_QUERY_CHARS + 1)}) is not None
    assert validate_kb_retrieve({"query": "x" * MAX_QUERY_CHARS}) is None


def test_validator_rejects_bad_top_k():
    """top_k 超限会拖垮服务，必须拦下。"""
    assert validate_kb_retrieve({"query": "a", "top_k": 0}) is not None
    assert validate_kb_retrieve({"query": "a", "top_k": -1}) is not None
    assert validate_kb_retrieve({"query": "a", "top_k": MAX_TOP_K + 1}) is not None
    assert validate_kb_retrieve({"query": "a", "top_k": "5"}) is not None
    assert validate_kb_retrieve({"query": "a", "top_k": True}) is not None
    assert validate_kb_retrieve({"query": "a", "top_k": 5}) is None


def test_validator_rejects_bad_kb_ids():
    assert validate_kb_retrieve({"query": "a", "kb_ids": "1,2"}) is not None
    assert validate_kb_retrieve({"query": "a", "kb_ids": ["1"]}) is not None
    assert validate_kb_retrieve({"query": "a", "kb_ids": [True]}) is not None
    assert validate_kb_retrieve({"query": "a", "kb_ids": list(range(60))}) is not None
    assert validate_kb_retrieve({"query": "a", "kb_ids": [1, 2]}) is None


# --------------------------------------------------------------------------- #
# 工具级授权
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_unregistered_tool_is_denied():
    registry = get_registry()
    result, record = await registry.invoke("rm_rf", FakeCtx())
    assert result is None
    assert record.allowed is False
    assert "未注册" in record.reason


@pytest.mark.asyncio
async def test_tool_denied_when_role_lacks_permission():
    """viewer 没有 retrieval:read 以外的工具权限；此处用自定义工具验证判定。"""
    registry = get_registry()
    registry.register(
        ToolSpec(
            name="admin_only",
            description="需要管理员权限的工具",
            permissions=frozenset({"admin:*"}),
            handler=_noop,
            validator=None,
        )
    )
    result, record = await registry.invoke("admin_only", FakeCtx(user=FakeUser(role="engineer")))
    assert result is None
    assert record.allowed is False
    assert "缺少工具权限" in record.reason


@pytest.mark.asyncio
async def test_admin_bypasses_tool_permission():
    registry = get_registry()
    registry.register(
        ToolSpec(
            name="admin_only",
            description="需要管理员权限的工具",
            permissions=frozenset({"admin:*"}),
            handler=_noop,
            validator=None,
        )
    )
    result, record = await registry.invoke("admin_only", FakeCtx(user=FakeUser(role="admin")))
    assert record.allowed is True
    assert result == "ok"


async def _noop(ctx, **params):
    return "ok"


@pytest.mark.asyncio
async def test_kb_retrieve_allowed_for_engineer():
    """engineer 拥有 retrieval:read，正常放行。"""
    ctx = FakeCtx(user=FakeUser(role="engineer"), allowed_kb_ids=[1])
    result, record = await get_registry().invoke("kb_retrieve", ctx, query="混凝土强度", kb_ids=[1])
    assert record.allowed is True, record.reason
    assert result is not None and "clauses" in result
    assert record.output_digest.startswith("clauses=")


@pytest.mark.asyncio
async def test_kb_retrieve_denied_for_role_without_permission():
    """没有任何检索权限的角色不得调用检索工具。"""
    registry = get_registry()
    result, record = await registry.invoke(
        "kb_retrieve", FakeCtx(user=FakeUser(role="unknown_role")), query="混凝土强度"
    )
    assert result is None
    assert record.allowed is False
    assert "缺少工具权限" in record.reason


# --------------------------------------------------------------------------- #
# 数据范围越权（最关键）
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_kb_retrieve_rejects_out_of_scope_kb_ids():
    """任务里存着越权的 kb_ids 时，工具层必须拦下（最后一道闸门）。"""
    ctx = FakeCtx(user=FakeUser(role="engineer"), allowed_kb_ids=[1, 2])
    result, record = await get_registry().invoke(
        "kb_retrieve", ctx, query="混凝土强度", kb_ids=[1, 99]
    )
    assert result is None, "越权范围不应被检索"
    assert record.allowed is False
    assert "越权" in record.reason
    # 关键：pipeline 根本没被调用（不产生任何数据泄露）
    assert ctx.pipeline.calls == []


@pytest.mark.asyncio
async def test_kb_retrieve_rejects_when_user_has_no_kb():
    """无任何可访问知识库时应「无依据」，而不是报错。

    报错会把整条链路打成 DEGRADED —— 修复过程中确实踩过这个坑：
    管理员 ``accessible_kb_ids`` 返回 None（表示不受限），
    若把 None 或空列表当成「空范围」直接报错，会把所有检索都拒掉。
    """
    ctx = FakeCtx(user=FakeUser(role="engineer"), allowed_kb_ids=[])
    result, record = await get_registry().invoke("kb_retrieve", ctx, query="混凝土强度")
    assert record.allowed is True, record.reason
    assert result is not None
    assert result["no_evidence"] is True
    assert result["clauses"] == []
    # 不触发实际检索（没有可查的库）
    assert ctx.pipeline.calls == []


@pytest.mark.asyncio
async def test_admin_with_no_requested_kb_searches_all():
    """管理员不指定 kb_ids 时必须是「不受限」（透传 None），而不是空范围。

    这是端到端验证抓出的回归：写成空范围会让管理员的所有检索被拒，
    表现为任务直接 DEGRADED、报告没有任何引用。
    """
    ctx = FakeCtx(user=FakeUser(role="admin"), allowed_kb_ids=[])
    result, record = await get_registry().invoke("kb_retrieve", ctx, query="混凝土强度")
    assert record.allowed is True, record.reason
    assert len(ctx.pipeline.calls) == 1
    assert ctx.pipeline.calls[0]["kb_ids"] is None, "管理员应为不受限（None）"


@pytest.mark.asyncio
async def test_kb_retrieve_converges_to_allowed_when_unspecified():
    """未指定 kb_ids 时收敛到授权集合，而非「全部知识库」。"""
    ctx = FakeCtx(user=FakeUser(role="engineer"), allowed_kb_ids=[7])
    result, record = await get_registry().invoke("kb_retrieve", ctx, query="混凝土强度")
    assert record.allowed is True, record.reason
    assert ctx.pipeline.calls[0]["kb_ids"] == [7]


@pytest.mark.asyncio
async def test_admin_retrieval_not_restricted():
    ctx = FakeCtx(user=FakeUser(role="admin"), allowed_kb_ids=[1])
    result, record = await get_registry().invoke(
        "kb_retrieve", ctx, query="混凝土强度", kb_ids=[99]
    )
    assert record.allowed is True, record.reason
    assert ctx.pipeline.calls[0]["kb_ids"] == [99]


# --------------------------------------------------------------------------- #
# 审计记录
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_audit_record_captures_allowed_and_denied():
    ctx = FakeCtx(user=FakeUser(uid=42, role="engineer"), allowed_kb_ids=[1])

    _, ok_record = await get_registry().invoke("kb_retrieve", ctx, query="强度", kb_ids=[1])
    _, bad_record = await get_registry().invoke("kb_retrieve", ctx, query="强度", kb_ids=[99])

    assert ok_record.allowed is True and ok_record.user_id == 42
    assert bad_record.allowed is False and bad_record.user_id == 42
    assert bad_record.reason
    # 参数摘要保留（便于事后追溯调用了什么）
    assert bad_record.params.get("kb_ids") == [99]


@pytest.mark.asyncio
async def test_audit_truncates_long_query():
    """审计表不能被超长 query 撑爆。"""
    ctx = FakeCtx(user=FakeUser(role="engineer"), allowed_kb_ids=[1])
    long_query = "x" * (MAX_QUERY_CHARS + 500)
    _, record = await get_registry().invoke("kb_retrieve", ctx, query=long_query, kb_ids=[1])
    # 参数校验先拦下（超长），审计里仍记录了截断后的参数
    assert record.allowed is False
    assert len(str(record.params.get("query"))) < len(long_query)


@pytest.mark.asyncio
async def test_handler_exception_becomes_denied_record():
    """工具内部异常不应冒泡中断 Agent，而是转成被拒绝的记录。"""
    ctx = FakeCtx(
        user=FakeUser(role="engineer"),
        allowed_kb_ids=[1],
        pipeline=FakePipeline(raises=RuntimeError("bm25 index missing")),
    )
    result, record = await get_registry().invoke("kb_retrieve", ctx, query="强度", kb_ids=[1])
    assert result is None
    assert record.allowed is False
    assert "执行失败" in record.reason


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
def test_registry_has_kb_retrieve_registered():
    registry = get_registry()
    assert "kb_retrieve" in registry.names()
    spec = registry.get("kb_retrieve")
    assert spec is not None
    assert "retrieval:read" in spec.permissions


def test_registry_rejects_duplicate_registration():
    registry = get_registry()
    with pytest.raises(ValueError, match="重复注册"):
        registry.register(
            ToolSpec(
                name="kb_retrieve",
                description="dup",
                permissions=frozenset(),
                handler=_noop,
                validator=None,
            )
        )


# --------------------------------------------------------------------------- #
# 节点级验证：retrieval_node 必须经注册表调用（而不是绕过授权直接调 pipeline）
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_retrieval_node_goes_through_registry():
    """确保检索节点走工具注册表 —— 这是授权闸门真正生效的前提。

    如果将来有人把 retrieval_node 改回直接调 ``ctx.pipeline.retrieve``，
    越权校验就会被绕过，本用例会失败。
    """
    from app.agent.graph import AgentContext, agent_context, retrieval_node
    from app.agent.guards import ConvergenceGuard

    pipeline = FakePipeline()
    ctx = AgentContext(
        guard=ConvergenceGuard(),
        session=FakeSession([1]),
        pipeline=pipeline,
        timings={},
        user=FakeUser(role="engineer"),
    )

    state = {
        "subtasks": [{"name": "混凝土强度", "query": "混凝土强度", "specialty": "结构工程"}],
        "options": {"kb_ids": [1]},
        "payload": {},
    }

    with agent_context(ctx):
        await retrieval_node(state)

    # 工具被调用并留下审计记录
    assert len(ctx.tool_calls) == 1, "检索节点应产生一条工具调用审计"
    assert ctx.tool_calls[0].tool == "kb_retrieve"
    assert ctx.tool_calls[0].allowed is True


@pytest.mark.asyncio
async def test_retrieval_node_blocks_out_of_scope_kb():
    """任务里存着越权 kb_ids 时，节点级调用必须不放行、不产生检索。"""
    from app.agent.graph import AgentContext, agent_context, retrieval_node
    from app.agent.guards import ConvergenceGuard

    pipeline = FakePipeline()
    ctx = AgentContext(
        guard=ConvergenceGuard(),
        session=FakeSession([1]),  # 只授权 kb 1
        pipeline=pipeline,
        timings={},
        user=FakeUser(role="engineer"),
    )
    state = {
        "subtasks": [{"name": "混凝土强度", "query": "混凝土强度"}],
        "options": {"kb_ids": [1, 99]},  # 99 越权
        "payload": {},
    }

    with agent_context(ctx):
        result = await retrieval_node(state)

    assert pipeline.calls == [], "越权范围不应触发实际检索"
    assert ctx.tool_calls and ctx.tool_calls[0].allowed is False
    # 节点应降级而非崩溃
    assert result.get("degraded") is True or result.get("retrieval_results")
