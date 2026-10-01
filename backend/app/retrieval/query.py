# -*- coding: utf-8 -*-
"""查询理解与 Multi-Query 改写（SRS FR-RET-02/03）。

查询理解使用规则 + 词典，保证无 LLM 时仍可用且低延迟；Multi-Query 使用 LLM，
失败时回退到规则模板，保证检索链路不中断。
"""
from __future__ import annotations

import re
from typing import Optional, Sequence

from app.config import settings
from app.core.logging_conf import get_logger
from app.llm.gateway import ChatMessage, get_llm
from app.retrieval.types import QueryUnderstanding

logger = get_logger(__name__)

#: 意图关键词表
INTENT_PATTERNS: dict[str, tuple[str, ...]] = {
    "clause_lookup": ("条款", "第几条", "哪一条", "规定", "条文"),
    "requirement": ("要求", "应", "不应", "宜", "不宜", "必须", "限值", "标准值", "允许偏差"),
    "criterion": ("判定", "合格", "验收", "标准是", "依据什么", "如何判定", "怎么判"),
    "process": ("流程", "程序", "步骤", "如何做", "怎么做", "报验", "见证"),
    "penalty": ("处罚", "责任", "后果", "违规"),
}

SPECIALTIES = (
    "结构工程", "地基基础", "主体结构", "装饰装修", "屋面工程", "给排水",
    "暖通空调", "电气工程", "智能化", "通风与空调", "建筑节能", "市政道路",
    "桥梁工程", "隧道工程", "钢结构", "混凝土", "砌体",
)

CLAUSE_RE = re.compile(r"\d+(?:\.\d+){1,3}")
SPEC_CODE_RE = re.compile(r"(?:GB|GBT|GB/T|JGJ|JGT|JG/T|CJJ|CJ/T|DB\d*|T/CECS|CECS)\s*/?\s*[A-Z]*\s*\d+[\-—]\d{2,4}", re.I)

_MULTI_QUERY_SYSTEM = (
    "你是工程监理规范检索助手。请把用户问题改写为 3-5 个适合检索国家标准/行业规范全文的子查询。"
    "要求：1) 使用规范书面术语，替换口语表达；2) 覆盖不同表述与上位概念；"
    "3) 每个子查询独立可检索，不包含“请”“帮我”等语气词；4) 只输出 JSON。"
)


def understand_query(
    query: str, *, specialty: Optional[str] = None, kb_ids: Optional[Sequence[int]] = None
) -> QueryUnderstanding:
    """规则化查询理解：意图分类 + 实体/约束抽取 + 关键词。"""
    text = (query or "").strip()
    understanding = QueryUnderstanding(original=text)

    scores = {
        intent: sum(1 for kw in kws if kw in text) for intent, kws in INTENT_PATTERNS.items()
    }
    best_intent = max(scores, key=lambda k: scores[k]) if scores else "clause_lookup"
    understanding.intent = best_intent if scores.get(best_intent, 0) > 0 else "clause_lookup"

    understanding.specialty = specialty or next((s for s in SPECIALTIES if s in text), None)
    understanding.clause_candidates = CLAUSE_RE.findall(text)
    understanding.entities = _extract_entities(text)
    understanding.keywords = _extract_keywords(text)
    understanding.constraints = _extract_constraints(text)
    return understanding


def _extract_entities(text: str) -> list[str]:
    entities: list[str] = []
    spec = SPEC_CODE_RE.search(text)
    if spec:
        entities.append(spec.group(0))
    for specialty in SPECIALTIES:
        if specialty in text and specialty not in entities:
            entities.append(specialty)
    for match in re.findall(r"[\u4e00-\u9fff]{2,8}(?:混凝土|钢筋|砂浆|模板|砌体|防水|保温|管线|幕墙)", text):
        if match not in entities:
            entities.append(match)
    return entities[:10]


def _extract_keywords(text: str) -> list[str]:
    import jieba

    jieba.setLogLevel(60)
    keywords = [
        token
        for token in jieba.lcut(text)
        if len(token) >= 2 and not token.isdigit() and token.strip()
    ]
    seen: list[str] = []
    for token in keywords:
        if token not in seen:
            seen.append(token)
    return seen[:12]


def _extract_constraints(text: str) -> dict:
    constraints: dict = {}
    numbers = re.findall(r"(\d+(?:\.\d+)?)\s*(℃|°C|mm|m|MPa|kN|%|天|d|小时|h|min|次)", text)
    if numbers:
        constraints["numeric_limits"] = [{"value": float(v), "unit": u} for v, u in numbers[:6]]
    for level in ("一级", "二级", "三级", "四级", "甲级", "乙级", "丙级"):
        if level in text:
            constraints["grade"] = level
            break
    if any(word in text for word in ("强制性", "强条", "必须")):
        constraints["mandatory"] = True
    return constraints


async def generate_sub_queries(
    query: str,
    understanding: Optional[QueryUnderstanding] = None,
    *,
    n: Optional[int] = None,
    enabled: Optional[bool] = None,
) -> list[str]:
    """Multi-Query 改写（SRS FR-RET-03）。LLM 失败时回退规则模板。"""
    n = n or settings.multi_query_n
    enabled = settings.multi_query_enabled if enabled is None else enabled
    original = (query or "").strip()
    if not enabled or not original:
        return [original]

    understanding = understanding or understand_query(original)
    prompt = (
        f"用户问题：{original}\n"
        f"识别到的意图：{understanding.intent}\n"
        f"识别到的实体：{'、'.join(understanding.entities) or '无'}\n"
        f"目标子查询数量：{n}\n"
        '输出格式：{"queries": ["子查询1", "子查询2", ...]}'
    )
    try:
        data, _ = await get_llm().chat_json(
            [ChatMessage("system", _MULTI_QUERY_SYSTEM), ChatMessage("user", prompt)],
            scene="multi_query",
            temperature=0.2,
            max_tokens=512,
        )
        queries = data.get("queries") if isinstance(data, dict) else data
        cleaned = _dedupe([str(q).strip() for q in (queries or []) if str(q).strip()])
        if cleaned:
            # 原问题始终排第一，保证不因改写丢失原始语义
            if original not in cleaned:
                cleaned.insert(0, original)
            return cleaned[: max(n, 1) + 1]
        logger.warning("Multi-Query 返回为空，使用规则改写", extra={"query": original[:80]})
    except Exception as exc:  # noqa: BLE001
        logger.warning("Multi-Query 改写失败，使用规则改写", extra={"error": str(exc)[:200]})
    return _template_queries(original, understanding, n)


def _template_queries(query: str, understanding: QueryUnderstanding, n: int) -> list[str]:
    """规则兜底改写：补充规范术语、上位概念与标准表述。"""
    templates = [
        query,
        f"{query} 规范条文",
        f"{query} 验收标准 允许偏差",
        f"{query} 强制性条文 要求",
        f"{query} 施工质量验收规范 规定",
    ]
    if understanding.specialty:
        templates.insert(2, f"{understanding.specialty} {query}")
    for entity in understanding.entities[:2]:
        templates.append(f"{entity} {query}")
    if understanding.clause_candidates:
        templates.insert(1, f"条款 {' '.join(understanding.clause_candidates)} 要求")
    return _dedupe(templates)[: max(n, 1) + 1]


def _dedupe(items: Sequence[str]) -> list[str]:
    seen: list[str] = []
    for item in items:
        if item and item not in seen:
            seen.append(item)
    return seen


__all__ = ["understand_query", "generate_sub_queries", "INTENT_PATTERNS", "SPEC_CODE_RE"]
