# -*- coding: utf-8 -*-
"""诊断/校验数据库结构状态（容器内或本机均可运行）。

用途：
- 确认当前库是「全新库 / 存量库（未纳管）/ 已纳管」；
- 迁移后核对关键字段是否存在（例如 knowledge_base.project_id）。

这是排查「种子数据初始化失败」的第一站：绝大多数情况是结构落后于代码。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from sqlalchemy import inspect  # noqa: E402

from app.db.session import (  # noqa: E402
    alembic_has_version,
    existing_table_count,
    get_engine,
    ping,
    resolve_schema_mode,
)

#: 各迁移引入的关键结构，用于逐项核对
EXPECTED = {
    "knowledge_base": ["project_id", "owner_id"],
    "agent_tool_call": None,  # 整表
}


def main() -> int:
    if not ping():
        print("[X] 数据库不可达")
        return 1

    engine = get_engine()
    tables = set(inspect(engine).get_table_names())
    print("=== 结构状态 ===")
    print(f"  表结构模式      : {resolve_schema_mode()}")
    print(f"  业务表数量      : {existing_table_count()}")
    print(f"  已纳管迁移版本  : {alembic_has_version()}")

    if not alembic_has_version() and existing_table_count() > 0:
        print("  ⚠️  判定为【存量库未纳管】：启动时会自动 stamp 基线再升级")
    elif not tables:
        print("  判定为【全新库】：启动时会从 0001 建到最新")
    else:
        print("  判定为【已纳管】：启动时按迁移链升级")

    print()
    print("=== 关键结构核对 ===")
    problems: list[str] = []
    for table, columns in EXPECTED.items():
        if table not in tables:
            problems.append(f"缺表 {table}")
            print(f"  [X] 缺表 {table}")
            continue
        if columns is None:
            print(f"  [OK] 表存在 {table}")
            continue
        actual = {c["name"] for c in inspect(engine).get_columns(table)}
        missing = [c for c in columns if c not in actual]
        if missing:
            problems.append(f"{table} 缺列 {missing}")
            print(f"  [X] {table} 缺列 {missing}")
        else:
            print(f"  [OK] {table} 含 {columns}")

    print()
    if problems:
        print(f"[FAIL] {len(problems)} 项缺失：{problems}")
        print("       修复：python -m alembic stamp 0001 && python -m alembic upgrade head")
        print("       （或直接重启 api 容器，入口会自动纳管并升级）")
        return 1
    print("[OK] 结构完整，与当前代码匹配")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
