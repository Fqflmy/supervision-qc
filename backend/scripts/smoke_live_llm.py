# -*- coding: utf-8 -*-
"""真实大模型连通性与结构化输出测试（DeepSeek）。

只验证模型链路本身，不触碰检索与图谱：
1. 基础对话是否可用、延迟与 Token；
2. chat_json 结构化输出是否稳定解析；
3. Multi-Query 中文术语改写质量（关键：把口语问题转成规范术语）。
"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.core.logging_conf import setup_logging  # noqa: E402
from app.llm.gateway import ChatMessage, get_llm, start_usage_session, current_usage  # noqa: E402
from app.retrieval.query import generate_sub_queries, understand_query  # noqa: E402


async def main() -> int:
    setup_logging("WARNING", json_output=False)
    gateway = get_llm()
    print(f"provider={settings.llm_provider} model={settings.llm_primary_model}")
    print(f"base_url={settings.llm_primary_base_url}")
    print(f"api_key={'已配置(' + settings.llm_primary_api_key[:7] + '...)' if settings.llm_primary_api_key else '未配置'}")
    if settings.llm_provider == "fake":
        print("[FAIL] 当前是 Fake 模式，请确认 backend/.env 中 SUPERVISION_LLM_PROVIDER=openai_compatible")
        return 2

    start_usage_session()
    failures = 0

    # 1) 基础对话
    print("\n[1] 基础对话")
    try:
        started = time.perf_counter()
        response = await gateway.chat(
            [
                ChatMessage("system", "你是工程监理规范助手，回答简洁。"),
                ChatMessage("user", "混凝土结构工程施工质量验收规范的编号是什么？一句话回答。"),
            ],
            scene="smoke_chat",
            max_tokens=200,
        )
        elapsed = time.perf_counter() - started
        print(f"  OK 延迟={elapsed:.2f}s tokens={response.usage.total_tokens} model={response.model}")
        print(f"  回答：{response.content.strip()[:160]}")
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"  FAIL {type(exc).__name__}: {str(exc)[:300]}")

    # 2) 结构化输出
    print("\n[2] 结构化输出（chat_json）")
    try:
        data, response = await gateway.chat_json(
            [
                ChatMessage("system", '只输出 JSON，结构：{"clause_no": "条款号", "requirement": "要求简述"}'),
                ChatMessage("user", "从这句话里提取条款号与要求：5.3.3 混凝土浇筑时的入模温度不宜高于30℃。"),
            ],
            scene="smoke_json",
            max_tokens=300,
        )
        print(f"  OK 解析结果：{json.dumps(data, ensure_ascii=False)[:200]}")
        if not isinstance(data, dict) or "clause_no" not in data:
            print("  WARN 结构不符合预期（但已成功解析）")
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"  FAIL {type(exc).__name__}: {str(exc)[:300]}")

    # 3) Multi-Query 中文术语改写
    print("\n[3] Multi-Query 改写质量（口语 → 规范术语）")
    queries = [
        "楼板浇完混凝土太热了会不会有问题",
        "脚手架那个连着墙的杆子要多远一个",
    ]
    for query in queries:
        understanding = understand_query(query)
        try:
            subs = await generate_sub_queries(query, understanding, n=4)
            print(f"  原问题：{query}")
            print(f"  意图：{understanding.intent}")
            for idx, item in enumerate(subs, start=1):
                print(f"    {idx}. {item}")
            if subs and subs[0] == query and len(subs) == 1:
                print("  WARN 未发生改写（退化为规则模板）")
        except Exception as exc:  # noqa: BLE001
            failures += 1
            print(f"  FAIL {type(exc).__name__}: {str(exc)[:200]}")

    usage = current_usage()
    print(f"\n累计 Token：{usage.as_dict()}")
    if failures:
        print(f"\n[FAIL] {failures} 项未通过")
        return 1
    print("\n[OK] 真实模型链路验证通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
