# -*- coding: utf-8 -*-
"""开发期冒烟脚本：验证基础层（DB 建表、JSON 解析、Fake LLM、安全模块）。"""
from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import inspect  # noqa: E402

from app.core.logging_conf import setup_logging  # noqa: E402
from app.core.security import (  # noqa: E402
    create_access_token,
    decode_token,
    hash_password,
    verify_password,
)
from app.db.session import get_engine, init_db, ping  # noqa: E402
from app.llm.gateway import FakeLlmGateway, current_usage, start_usage_session  # noqa: E402
from app.llm.json_utils import extract_json  # noqa: E402


async def check_llm() -> None:
    gateway = FakeLlmGateway()
    start_usage_session()
    for scene in (
        "agent_planning",
        "agent_matching",
        "agent_analysis",
        "agent_report",
        "judge_score",
        "multi_query",
        "kg_extract",
    ):
        data, _ = await gateway.chat_json(
            [{"role": "user", "content": "任务拆解 原问题：混凝土浇筑温度控制"}], scene=scene
        )
        preview = json.dumps(data, ensure_ascii=False)[:100]
        print(f"  {scene:16s} -> {preview}")
    print("  usage:", current_usage().as_dict(), "calls:", gateway.call_count)


def main() -> int:
    setup_logging("WARNING", json_output=False)
    started = time.time()

    print("[1] 数据库")
    print("  ping:", ping())
    print("  init:", init_db())
    tables = sorted(inspect(get_engine()).get_table_names())
    print(f"  tables({len(tables)}): {', '.join(tables)}")

    print("[2] JSON 提取")
    messy = '好的，结果如下：\n```json\n{"a": [1, 2,], "b": "中文", }\n```\n希望有帮助'
    print("  fence+trailing comma:", extract_json(messy))
    print("  plain text prefix:", extract_json('prefix {"x": {"y": 1}} suffix'))
    print("  chinese quotes:", extract_json('{"name": “测试条款”}'))

    print("[3] Fake LLM 结构化输出")
    asyncio.run(check_llm())

    print("[4] 安全模块")
    hashed = hash_password("Abcd1234")
    print("  bcrypt:", verify_password("Abcd1234", hashed), verify_password("bad", hashed))
    token = create_access_token(7, roles=["admin", "engineer"])
    payload = decode_token(token)
    print("  jwt sub/roles:", payload["sub"], payload["roles"])
    long_pw = "长" * 60 + "Ab1"
    print("  >72byte password handled:", verify_password(long_pw, hash_password(long_pw)))

    print(f"[OK] 基础层验证通过，耗时 {time.time() - started:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
