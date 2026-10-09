# -*- coding: utf-8 -*-
"""Agent 工具注册表：工具级权限、参数校验与调用审计。

设计目标（对应权限设计的「工具调用权限」）
------------------------------------------
1. **工具级授权**：每个工具声明所需权限点，调用前校验；未授权直接拒绝，
   不执行也不产生副作用；
2. **参数校验**：工具参数在执行前做白名单校验，防止越权查询
   （例如把 ``kb_ids`` 改成他人项目的知识库、把 ``top_k`` 改成超大值拖垮服务）；
3. **调用审计**：每次调用都记录一条审计（工具名、参数摘要、是否放行、
   拒绝原因、耗时），供事后追溯。

为什么需要它
------------
Agent 的检索节点原先直接用请求里的 ``kb_ids`` 调 ``pipeline.retrieve()``，
**没有任何二次校验**。虽然创建任务时已校验过知识库授权，但任务一旦落库，
其 ``options.kb_ids`` 就成为可信输入 —— 若有人直接改库或后续新增入口，
Agent 就会拿越权范围去检索。工具层校验是最后一道闸门。

使用方式
--------
    registry = get_registry()
    result, record = await registry.invoke(
        "kb_retrieve", ctx, query="混凝土强度", kb_ids=[1, 2]
    )

``invoke`` 永不抛出权限/参数异常，而是返回 ``record.allowed=False`` 与原因，
由调用方决定如何降级（Agent 需要继续跑完并出报告，不能因单个工具失败而中断）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Optional, Sequence

from app.constants import AuditAction
from app.core.authz import accessible_kb_ids, is_admin, project_ids_of, user_id_of
from app.core.logging_conf import get_logger

logger = get_logger(__name__)

#: 参数上限，防止超大入参拖垮服务
MAX_QUERY_CHARS = 2000
MAX_TOP_K = 50
MAX_KB_IDS = 50


@dataclass
class ToolSpec:
    """工具定义。"""

    name: str
    description: str
    #: 所需权限点（任一命中即通过）
    permissions: frozenset[str]
    handler: Callable[..., Awaitable[Any]]
    #: 参数校验器：返回错误信息（None 表示通过）
    validator: Callable[[dict], Optional[str]]


@dataclass
class ToolCallRecord:
    """一次工具调用的审计记录。"""

    tool: str
    allowed: bool
    params: dict = field(default_factory=dict)
    reason: str = ""
    duration_ms: int = 0
    user_id: Optional[int] = None
    output_digest: str = ""

    def to_dict(self) -> dict:
        return {
            "tool": self.tool,
            "allowed": self.allowed,
            "params": self.params,
            "reason": self.reason,
            "duration_ms": self.duration_ms,
            "output_digest": self.output_digest,
        }


def _summarize(params: dict, limit: int = 200) -> dict:
    """参数摘要：截断长文本，避免审计表被超长 query 撑爆。"""
    out: dict = {}
    for key, value in (params or {}).items():
        if isinstance(value, str):
            out[key] = value if len(value) <= limit else value[:limit] + f"…(len={len(value)})"
        elif isinstance(value, (list, tuple)):
            items = list(value)[:20]
            out[key] = items + (["…"] if len(value) > 20 else [])
        else:
            out[key] = value
    return out


# --------------------------------------------------------------------------- #
# 参数校验器
# --------------------------------------------------------------------------- #
def validate_kb_retrieve(params: dict) -> Optional[str]:
    query = params.get("query")
    if not isinstance(query, str) or not query.strip():
        return "缺少必填参数 query"
    if len(query) > MAX_QUERY_CHARS:
        return f"query 过长（{len(query)} > {MAX_QUERY_CHARS}）"

    top_k = params.get("top_k", 5)
    if not isinstance(top_k, int) or isinstance(top_k, bool) or top_k < 1:
        return f"top_k 非法：{top_k!r}"
    if top_k > MAX_TOP_K:
        return f"top_k 超出上限（{top_k} > {MAX_TOP_K}）"

    kb_ids = params.get("kb_ids")
    if kb_ids is not None:
        if not isinstance(kb_ids, (list, tuple)):
            return "kb_ids 必须是数组"
        if len(kb_ids) > MAX_KB_IDS:
            return f"kb_ids 数量超限（{len(kb_ids)} > {MAX_KB_IDS}）"
        for item in kb_ids:
            if not isinstance(item, int) or isinstance(item, bool):
                return f"kb_ids 含非整数项：{item!r}"
    return None


# --------------------------------------------------------------------------- #
# 工具处理函数
# --------------------------------------------------------------------------- #
async def _tool_kb_retrieve(ctx: Any, **params: Any) -> dict:
    """规范检索（受知识库授权约束）。

    这是 Agent 最核心的工具，也是越权风险最高的一个：
    检索范围必须落在调用者被授权的知识库集合内。

    范围语义（务必区分「不受限」与「空范围」）：
    - ``accessible_kb_ids`` 返回 ``None`` 表示**不受限**（管理员）→ 原样透传，
      由 pipeline 跨全部知识库检索；
    - 返回列表表示**受限于该集合** → 请求范围必须是子集，未指定则收敛到该集合；
    - 返回**空列表**表示该用户没有任何可访问知识库 → 无依据，但**不报错**，
      让流程正常降级为 ``no_evidence``（报错会让整条链路 DEGRADED）。
    """
    session = ctx.session
    user = getattr(ctx, "user", None)
    allowed = accessible_kb_ids(session, user)
    requested = params.get("kb_ids")

    if allowed is None:
        # 不受限：不指定就不限；指定则按请求范围
        effective = sorted({int(x) for x in requested}) if requested else None
    else:
        allowed_set = set(allowed)
        if requested:
            illegal = {int(x) for x in requested} - allowed_set
            if illegal:
                # 不在工具内抛错：由 invoke 统一转成「拒绝 + 审计」
                raise PermissionError(f"检索范围含未授权知识库：{sorted(illegal)}")
            effective = sorted({int(x) for x in requested})
        elif allowed_set:
            effective = sorted(allowed_set)
        else:
            # 无任何可访问知识库：返回「无依据」而非报错
            return {
                "no_evidence": True,
                "latency_ms": 0,
                "clauses": [],
                "sub_queries": [],
                "denied_reason": "当前用户没有任何可访问的知识库",
            }

    result = await ctx.pipeline.retrieve(
        params["query"],
        session,
        kb_ids=effective,
        specialty=params.get("specialty"),
        top_k=params.get("top_k", 5),
    )
    return {
        "no_evidence": result.no_evidence,
        "latency_ms": result.latency_ms,
        "clauses": [c.to_dict() for c in result.clauses],
        "sub_queries": result.sub_queries,
    }


# --------------------------------------------------------------------------- #
# 注册表
# --------------------------------------------------------------------------- #
class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> None:
        if spec.name in self._tools:
            raise ValueError(f"工具重复注册：{spec.name}")
        self._tools[spec.name] = spec

    def names(self) -> list[str]:
        return sorted(self._tools)

    def get(self, name: str) -> Optional[ToolSpec]:
        return self._tools.get(name)

    def has_permission(self, user: Any, spec: ToolSpec) -> bool:
        """工具级授权：管理员直通，否则按权限点判定。"""
        if is_admin(user):
            return True
        if not spec.permissions:
            return True
        try:
            from app.api.deps import ROLE_PERMISSIONS

            grants = ROLE_PERMISSIONS.get(str(getattr(user, "role", "")).lower(), set())
        except Exception:  # noqa: BLE001
            grants = set()
        if "*" in grants or "admin:*" in grants:
            return True
        return bool(spec.permissions & grants)

    async def invoke(self, name: str, ctx: Any, **params: Any) -> tuple[Any, ToolCallRecord]:
        """调用工具。返回 (结果, 审计记录)。

        权限或参数不通过时**不抛异常**：Agent 需要继续跑完并产出报告
        （降级而非中断），因此把拒绝信息放进审计记录由调用方处理。
        """
        user = getattr(ctx, "user", None)
        record = ToolCallRecord(
            tool=name,
            allowed=False,
            params=_summarize(params),
            user_id=user_id_of(user),
        )

        spec = self._tools.get(name)
        if spec is None:
            record.reason = f"未注册的工具：{name}"
            logger.warning("Agent 调用了未注册的工具", extra={"tool": name})
            return None, record

        if not self.has_permission(user, spec):
            record.reason = f"缺少工具权限：{sorted(spec.permissions)}"
            logger.warning(
                "Agent 工具调用被拒绝（权限不足）",
                extra={"tool": name, "role": getattr(user, "role", None)},
            )
            return None, record

        error = spec.validator(params) if spec.validator else None
        if error:
            record.reason = f"参数校验失败：{error}"
            logger.warning("Agent 工具参数校验失败", extra={"tool": name, "error": error})
            return None, record

        started = time.perf_counter()
        try:
            result = await spec.handler(ctx, **params)
        except PermissionError as exc:
            record.reason = f"数据范围越权：{exc}"
            record.duration_ms = int((time.perf_counter() - started) * 1000)
            logger.warning(
                "Agent 工具数据范围越权被拒绝",
                extra={"tool": name, "reason": str(exc)[:200]},
            )
            return None, record
        except Exception as exc:  # noqa: BLE001
            record.reason = f"执行失败：{type(exc).__name__}: {str(exc)[:200]}"
            record.duration_ms = int((time.perf_counter() - started) * 1000)
            return None, record

        record.allowed = True
        record.duration_ms = int((time.perf_counter() - started) * 1000)
        record.output_digest = _digest_of(result)
        return result, record


def _digest_of(result: Any) -> str:
    """结果摘要：只记规模，不记内容（审计表保持轻量）。"""
    if isinstance(result, dict):
        clauses = result.get("clauses")
        if isinstance(clauses, list):
            return f"clauses={len(clauses)}, no_evidence={result.get('no_evidence')}"
    return type(result).__name__


_registry: Optional[ToolRegistry] = None


def get_registry() -> ToolRegistry:
    """获取工具注册表（单例）。"""
    global _registry
    if _registry is None:
        registry = ToolRegistry()
        registry.register(
            ToolSpec(
                name="kb_retrieve",
                description="规范知识库混合检索（受调用者知识库授权约束）",
                permissions=frozenset({"retrieval:read"}),
                handler=_tool_kb_retrieve,
                validator=validate_kb_retrieve,
            )
        )
        _registry = registry
    return _registry


def reset_registry() -> None:
    global _registry
    _registry = None
