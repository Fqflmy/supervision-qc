# -*- coding: utf-8 -*-
"""LLM 网关（SRS 2.3 / FR-SYS-04）。

能力：
- OpenAI 兼容协议调用 DeepSeek / Qwen(DashScope 兼容模式) / vLLM 等；
- 指数退避重试（默认 1s/2s/4s，≤3 次）；
- 主模型失败自动切换备用模型（SRS 7.2 故障转移）；
- JSON 结构化输出解析（含 json_repair 兜底）；
- Token 用量累积（每次请求一个 UsageContext，供 Agent Token 预算使用）。
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from contextvars import ContextVar
from typing import Any, Optional, Sequence

from app.config import settings
from app.core.errors import LLMError, StructuredOutputError
from app.core.logging_conf import get_logger
from app.llm.base import ChatMessage, LlmResponse, Usage
from app.llm.json_utils import extract_json
from app.llm.tokens import count_tokens

logger = get_logger(__name__)

_usage_ctx: ContextVar[Optional[Usage]] = ContextVar("llm_usage", default=None)


def start_usage_session() -> Usage:
    """开始一次用量统计会话（用于 Agent Token 预算，SRS 4.3）。"""
    usage = Usage()
    _usage_ctx.set(usage)
    return usage


def current_usage() -> Usage:
    return _usage_ctx.get() or Usage()


def _accumulate(usage: Usage) -> None:
    current = _usage_ctx.get()
    if current is not None:
        current.prompt_tokens += usage.prompt_tokens
        current.completion_tokens += usage.completion_tokens
        current.total_tokens += usage.total_tokens


class ModelTarget:
    __slots__ = ("model", "base_url", "api_key", "label")

    def __init__(self, model: str, base_url: str, api_key: str, label: str) -> None:
        self.model = model
        self.base_url = base_url
        self.api_key = api_key
        self.label = label

    @property
    def available(self) -> bool:
        return bool(self.model and self.base_url)


class LlmGateway:
    """统一 LLM 入口。"""

    def __init__(
        self,
        *,
        primary: Optional[ModelTarget] = None,
        fallback: Optional[ModelTarget] = None,
        timeout: Optional[float] = None,
        max_retries: Optional[int] = None,
    ) -> None:
        self.primary = primary or ModelTarget(
            settings.llm_primary_model,
            settings.llm_primary_base_url,
            settings.llm_primary_api_key,
            "primary",
        )
        self.fallback = fallback or ModelTarget(
            settings.llm_fallback_model,
            settings.llm_fallback_base_url,
            settings.llm_fallback_api_key,
            "fallback",
        )
        self.timeout = timeout if timeout is not None else settings.llm_timeout_seconds
        self.max_retries = max_retries if max_retries is not None else settings.llm_max_retries
        self._semaphore = asyncio.Semaphore(max(1, settings.llm_concurrency))

    # ------------------------------------------------------------------ #
    # 对外接口
    # ------------------------------------------------------------------ #
    async def chat(
        self,
        messages: Sequence[ChatMessage] | Sequence[dict],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        scene: str = "generic",
        allow_fallback: Optional[bool] = None,
    ) -> LlmResponse:
        payload = self._normalize(messages)
        targets = [self.primary]
        use_fallback = settings.llm_enable_fallback if allow_fallback is None else allow_fallback
        if use_fallback and self.fallback.available and self.fallback.base_url != self.primary.base_url:
            targets.append(self.fallback)

        # 追踪整次 chat 调用（含重试与降级），span 上带 scene/model/token，
        # 便于在 LangSmith 里按场景看耗时与成本。
        from app.core.tracing import trace_span

        with trace_span(
            f"llm.chat[{scene}]",
            run_type="llm",
            inputs={"scene": scene, "model": model or self.primary.model, "messages": payload},
            tags=["llm", scene],
            metadata={"provider": self.primary.base_url, "scene": scene},
        ) as span:
            return await self._chat_with_fallback(
                payload,
                targets=targets,
                model=model,
                temperature=temperature,
                max_tokens=max_tokens,
                scene=scene,
                span=span,
            )

    async def _chat_with_fallback(
        self,
        payload: list[ChatMessage],
        *,
        targets: list[ModelTarget],
        model: Optional[str],
        temperature: Optional[float],
        max_tokens: Optional[int],
        scene: str,
        span: Optional[Any] = None,
    ) -> LlmResponse:
        last_error: Optional[Exception] = None
        for target in targets:
            for attempt in range(1, self.max_retries + 1):
                try:
                    response = await self._call_once(
                        target,
                        payload,
                        model=model or target.model,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        scene=scene,
                        is_fallback=target.label == "fallback",
                    )
                    _accumulate(response.usage)
                    if span is not None:
                        span.outputs = {
                            "content": response.content,
                            "model": response.model,
                            "usage": {
                                "prompt_tokens": response.usage.prompt_tokens,
                                "completion_tokens": response.usage.completion_tokens,
                                "total_tokens": response.usage.total_tokens,
                            },
                            "latency_ms": response.latency_ms,
                            "is_fallback": response.is_fallback,
                        }
                    return response
                except Exception as exc:  # noqa: BLE001 - 统一转 LLMError
                    last_error = exc
                    logger.warning(
                        "LLM 调用失败",
                        extra={
                            "scene": scene,
                            "target": target.label,
                            "model": model or target.model,
                            "attempt": attempt,
                            "error": str(exc)[:300],
                        },
                    )
                    if attempt < self.max_retries:
                        backoff = settings.llm_retry_backoff_seconds * (2 ** (attempt - 1))
                        await asyncio.sleep(backoff)
        if span is not None:
            span.outputs = {"error": str(last_error)[:500], "scene": scene}
        raise LLMError(
            f"大模型调用失败，已尝试 {len(targets)} 个模型端点",
            details={"last_error": str(last_error)[:500], "scene": scene},
        )

    async def chat_json(
        self,
        messages: Sequence[ChatMessage] | Sequence[dict],
        *,
        schema_hint: Optional[str] = None,
        scene: str = "structured",
        max_repair_attempts: int = 2,
        **kwargs: Any,
    ) -> tuple[dict | list, LlmResponse]:
        """要求模型输出 JSON，并做解析修复（SRS 6.4 错误码 60003）。"""
        prepared = list(self._normalize(messages))
        if schema_hint:
            prepared.append(
                ChatMessage(
                    "system",
                    "只输出一个 JSON，不要任何解释文字或 Markdown 代码块外的内容。"
                    f"目标结构：{schema_hint}",
                )
            )
        last_error: Optional[Exception] = None
        response: Optional[LlmResponse] = None
        for attempt in range(max_repair_attempts + 1):
            response = await self.chat(prepared, scene=scene, **kwargs)
            try:
                return extract_json(response.content), response
            except Exception as exc:  # noqa: BLE001
                last_error = exc
                logger.warning(
                    "结构化输出解析失败，收紧提示词重试",
                    extra={"scene": scene, "attempt": attempt, "error": str(exc)[:200]},
                )
                prepared.append(ChatMessage("assistant", response.content[:2000]))
                prepared.append(
                    ChatMessage(
                        "user",
                        "上面的内容不是合法 JSON。请重新输出，仅返回合法 JSON，"
                        "不要 Markdown 代码块，不要注释，字符串内不要出现未转义的换行。",
                    )
                )
        raise StructuredOutputError(
            "结构化输出解析失败", details={"last_error": str(last_error)[:300], "scene": scene}
        )

    # ------------------------------------------------------------------ #
    # 内部实现
    # ------------------------------------------------------------------ #
    @staticmethod
    def _normalize(messages: Sequence[ChatMessage] | Sequence[dict]) -> list[ChatMessage]:
        out: list[ChatMessage] = []
        for item in messages:
            if isinstance(item, ChatMessage):
                out.append(item)
            elif isinstance(item, dict):
                out.append(ChatMessage(item.get("role", "user"), item.get("content", "")))
            else:  # pragma: no cover
                raise LLMError(f"不支持的 message 类型：{type(item)!r}")
        return out

    async def _call_once(
        self,
        target: ModelTarget,
        messages: list[ChatMessage],
        *,
        model: str,
        temperature: Optional[float],
        max_tokens: Optional[int],
        scene: str,
        is_fallback: bool,
    ) -> LlmResponse:
        if not target.base_url:
            raise LLMError(f"模型端点未配置：{target.label}")
        if not target.api_key and "localhost" not in target.base_url and "127.0.0.1" not in target.base_url:
            raise LLMError(
                f"缺少 API Key：请设置 SUPERVISION_LLM_{target.label.upper()}_API_KEY 环境变量"
            )

        from openai import AsyncOpenAI

        client = AsyncOpenAI(
            base_url=target.base_url,
            api_key=target.api_key or "sk-local",
            timeout=self.timeout,
            max_retries=0,
        )
        started = time.perf_counter()
        try:
            async with self._semaphore:
                completion = await client.chat.completions.create(
                    model=model,
                    messages=[m.to_dict() for m in messages],
                    temperature=settings.llm_temperature if temperature is None else temperature,
                    max_tokens=max_tokens or settings.llm_max_tokens,
                    stream=False,
                )
        finally:
            await client.close()

        latency_ms = int((time.perf_counter() - started) * 1000)
        choice = completion.choices[0] if completion.choices else None
        content = (choice.message.content if choice and choice.message else "") or ""
        raw_usage = getattr(completion, "usage", None)
        if raw_usage is not None:
            usage = Usage(
                prompt_tokens=getattr(raw_usage, "prompt_tokens", 0) or 0,
                completion_tokens=getattr(raw_usage, "completion_tokens", 0) or 0,
                total_tokens=getattr(raw_usage, "total_tokens", 0) or 0,
            )
        else:
            prompt_tokens = sum(count_tokens(m.content) for m in messages)
            completion_tokens = count_tokens(content)
            usage = Usage(prompt_tokens, completion_tokens, prompt_tokens + completion_tokens)

        if not content.strip():
            raise LLMError("模型返回空内容")

        logger.info(
            "LLM 调用完成",
            extra={
                "scene": scene,
                "model": model,
                "latency_ms": latency_ms,
                "total_tokens": usage.total_tokens,
                "is_fallback": is_fallback,
            },
        )
        return LlmResponse(
            content=content,
            model=model,
            provider="openai_compatible",
            usage=usage,
            latency_ms=latency_ms,
            is_fallback=is_fallback,
            finish_reason=getattr(choice, "finish_reason", None) if choice else None,
        )


# --------------------------------------------------------------------------- #
# 单例与测试替身
# --------------------------------------------------------------------------- #
_gateway: Optional[LlmGateway] = None


def get_llm() -> LlmGateway:
    global _gateway
    if _gateway is None:
        if settings.llm_provider == "fake":
            _gateway = FakeLlmGateway()  # type: ignore[assignment]
        else:
            _gateway = LlmGateway()
    return _gateway


def set_llm(gateway: Optional[LlmGateway]) -> None:
    """测试注入用。"""
    global _gateway
    _gateway = gateway


def reset_llm() -> None:
    global _gateway
    _gateway = None


class FakeLlmGateway(LlmGateway):
    """确定性替身：离线单测与无 Key 演示使用，不发起任何网络请求。

    按提示词中的结构提示返回可用的结构化数据，保证 Agent 全流程可跑通。
    """

    def __init__(self) -> None:  # noqa: D107
        super().__init__(primary=ModelTarget("fake-llm", "http://fake.local/v1", "sk-fake", "primary"))
        self.call_count = 0
        self.scene_calls: dict[str, int] = {}

    async def chat(  # type: ignore[override]
        self,
        messages: Sequence[ChatMessage] | Sequence[dict],
        *,
        model: Optional[str] = None,
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        scene: str = "generic",
        allow_fallback: Optional[bool] = None,
    ) -> LlmResponse:
        prepared = self._normalize(messages)
        self.call_count += 1
        self.scene_calls[scene] = self.scene_calls.get(scene, 0) + 1
        text = "\n".join(m.content for m in prepared)
        content = _fake_respond(scene, text)
        prompt_tokens = sum(count_tokens(m.content) for m in prepared)
        completion_tokens = count_tokens(content)
        usage = Usage(prompt_tokens, completion_tokens, prompt_tokens + completion_tokens)
        _accumulate(usage)
        return LlmResponse(
            content=content,
            model=model or "fake-llm",
            provider="fake",
            usage=usage,
            latency_ms=1,
        )


def _fake_respond(scene: str, text: str) -> str:
    """按 scene 精确分发（scene 优先，其次才用提示词关键词兜底）。"""
    lowered = text.lower()
    scene = (scene or "").strip().lower()

    if scene in {"agent_matching", "clause_matching", "matching"}:
        return _fake_match()
    if scene in {"agent_planning", "planning"}:
        return _fake_plan()
    if scene in {"agent_analysis", "analysis"}:
        return _fake_analysis()
    if scene in {"agent_report", "report", "report_generation"}:
        return _fake_report()
    if scene in {"judge_score", "judge"}:
        return _fake_judge()
    if scene in {"multi_query", "query_rewrite"}:
        return _fake_multiquery(text)
    if scene in {"kg_extract", "kg_entity_relation"}:
        return _fake_kg()
    if scene in {"retrieval_answer", "chat_answer"}:
        return json.dumps(
            {
                "answer": "依据检索到的规范条款，混凝土入模温度不宜高于 30℃。",
                "used_clause_nos": ["5.2.3"],
            },
            ensure_ascii=False,
        )
    if scene in {"citation_verify", "judge_citation"}:
        return json.dumps({"consistent": True, "reason": "引用条款与结论语义一致。"}, ensure_ascii=False)

    # ---- 关键词兜底（仅用于 scene 未识别的场景）----
    if "改写" in text and "queries" in lowered:
        return _fake_multiquery(text)
    if "verdict" in lowered and "reasoning" in lowered:
        return _fake_match()
    if "subtasks" in lowered:
        return _fake_plan()
    if "findings" in lowered:
        return _fake_analysis()
    if "overall_verdict" in lowered or "sections" in lowered:
        return _fake_report()
    if "dimension" in lowered and "score" in lowered:
        return _fake_judge()
    if "relations" in lowered and "entities" in lowered:
        return _fake_kg()
    return json.dumps({"result": "ok", "echo": text[:120]}, ensure_ascii=False)


def _fake_multiquery(text: str) -> str:
    base = _extract_after(text, ["原问题：", "问题：", "query:"]) or "混凝土浇筑质量控制要求"
    return json.dumps(
        {
            "queries": [
                base,
                f"{base} 相关规范条款",
                f"{base} 验收标准 允许偏差",
                f"{base} 强制性条文 要求",
            ]
        },
        ensure_ascii=False,
    )


def _fake_plan() -> str:
    return json.dumps(
        {
            "subtasks": [
                {
                    "name": "原材料进场检验与复验",
                    "criterion": "核查水泥、钢筋、外加剂等原材料出厂合格证与进场复验报告是否齐全有效",
                    "required_evidence": "出厂合格证、进场复验报告、见证取样记录",
                    "specialty": "结构工程",
                    "query": "原材料进场检验 复验 见证取样 规定",
                },
                {
                    "name": "混凝土浇筑与养护过程控制",
                    "criterion": "核查混凝土入模温度、坍落度、振捣与养护是否满足规范限值",
                    "required_evidence": "混凝土浇筑记录、测温记录、坍落度检测记录",
                    "specialty": "结构工程",
                    "query": "混凝土浇筑 入模温度 坍落度 养护 规定",
                },
                {
                    "name": "检验批验收与实体质量",
                    "criterion": "核查检验批主控项目与一般项目合格判定是否符合验收规范",
                    "required_evidence": "检验批质量验收记录、实体检测报告",
                    "specialty": "结构工程",
                    "query": "检验批 主控项目 一般项目 合格判定 标准",
                },
            ]
        },
        ensure_ascii=False,
    )


def _fake_match() -> str:
    return json.dumps(
        {
            "verdict": "partial",
            "confidence": 0.72,
            "evidence": "记录显示入模温度 32℃，高于规范 30℃ 限值；其余项目资料齐全。",
            "reasoning": "依据规范限值比对，温度指标超出允许范围，判定为部分符合。",
            "risk_level": "medium",
            "remediation": "调整配合比与浇筑时段，复测入模温度并留存测温记录后重新报验。",
        },
        ensure_ascii=False,
    )


def _fake_analysis() -> str:
    return json.dumps(
        {
            "summary": "本次评估共核查 3 项，1 项不符合、1 项部分符合，整体风险等级为中等。",
            "risk_level": "medium",
            "findings": [
                {
                    "clause_no": "5.2.3",
                    "problem": "混凝土入模温度超出规范限值",
                    "cause": "夏季施工未采取降温措施",
                    "risk_level": "medium",
                    "remediation": "调整浇筑时段并采取骨料降温措施",
                }
            ],
            "conclusion": "本次质量评估结论为「部分符合」，需整改后重新报验。",
        },
        ensure_ascii=False,
    )


def _fake_report() -> str:
    return json.dumps(
        {
            "title": "地下室剪力墙混凝土施工质量评估报告",
            "overview": "依据现行验收规范对地下室剪力墙施工质量进行智能初评。",
            "overall_verdict": "partial",
            "risk_level": "medium",
            "conclusion": "整体部分符合规范要求，存在 1 项温度控制不符合项，须整改后复验。",
            "sections": [
                {"heading": "一、评估概述", "content": "本次评估范围为地下室剪力墙混凝土分项工程。"},
                {"heading": "二、评估依据", "content": "GB 50204-2015《混凝土结构工程施工质量验收规范》等。"},
                {"heading": "三、逐条比对", "content": "详见条款比对表。"},
                {"heading": "四、问题与整改", "content": "入模温度超限，需采取降温措施并复测。"},
            ],
        },
        ensure_ascii=False,
    )


def _fake_judge() -> str:
    return json.dumps(
        {
            "scores": [
                {"dimension": "clause_citation_accuracy", "score": 4.0, "comment": "引用条款真实存在且与结论相关。"},
                {"dimension": "conclusion_reasonableness", "score": 4.0, "comment": "结论与证据一致。"},
                {"dimension": "evidence_sufficiency", "score": 3.5, "comment": "证据基本充分，缺少部分原始记录。"},
                {"dimension": "format_compliance", "score": 4.5, "comment": "结构完整，章节齐全。"},
                {"dimension": "remediation_actionability", "score": 4.0, "comment": "整改建议可执行。"},
            ]
        },
        ensure_ascii=False,
    )


def _fake_kg() -> str:
    return json.dumps(
        {
            "entities": [
                {"text": "混凝土入模温度", "label": "Indicator", "clause_no": "5.2.3"},
                {"text": "地下室剪力墙", "label": "Part", "clause_no": "5.2.3"},
                {"text": "GB 50204-2015", "label": "Spec", "clause_no": None},
            ],
            "relations": [
                {
                    "source": "GB 50204-2015",
                    "target": "混凝土入模温度",
                    "relation": "CONTAINS",
                    "evidence": "本规范规定了混凝土入模温度要求",
                }
            ],
        },
        ensure_ascii=False,
    )


def _extract_after(text: str, markers: Sequence[str]) -> Optional[str]:
    for marker in markers:
        idx = text.find(marker)
        if idx >= 0:
            tail = text[idx + len(marker) :].strip()
            line = tail.splitlines()[0].strip() if tail else ""
            if line:
                return line
    return None


__all__ = [
    "LlmGateway",
    "FakeLlmGateway",
    "ModelTarget",
    "get_llm",
    "set_llm",
    "reset_llm",
    "start_usage_session",
    "current_usage",
    "Usage",
    "ChatMessage",
    "LlmResponse",
]
