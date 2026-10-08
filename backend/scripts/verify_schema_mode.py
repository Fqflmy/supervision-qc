# -*- coding: utf-8 -*-
"""端到端验证：alembic 迁移模式能从零建库，且存量库可 stamp 纳管。

验证两条路径：
1. **全新库**：``schema_mode=alembic`` 时 ``init_db()`` 自动 upgrade head，
   建出 18 张业务表 + alembic_version；
2. **存量库**（此前由 create_all 建表）：先 create_all 建表，再 stamp head，
   确认不会重复建表、且后续 upgrade 成为空操作。

用法：
    python scripts/verify_schema_mode.py
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import create_engine, inspect, text  # noqa: E402

BASE_URL = "postgresql+psycopg://supervision:supervision@127.0.0.1:5432"
DB_NEW = "supervision_newdb"
DB_LEGACY = "supervision_legacydb"


def admin():
    return create_engine(f"{BASE_URL}/postgres", isolation_level="AUTOCOMMIT", pool_pre_ping=True)


def recreate(name: str) -> None:
    with admin().connect() as conn:
        conn.execute(
            text(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                "WHERE datname = :db AND pid <> pg_backend_pid()"
            ),
            {"db": name},
        )
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))


def load_app(db_name: str, mode: str = "alembic", environment: str = "production"):
    """切换应用配置到指定库并重载模块单例。

    必须真正 reload：``importlib.import_module`` 在模块已导入时返回缓存对象，
    仅改环境变量不会生效，上一组测试的引擎会被复用（表现为查到别的库的数据）。
    """
    os.environ["SUPERVISION_DATABASE_URL"] = f"{BASE_URL}/{db_name}"
    os.environ["SUPERVISION_DB_SCHEMA_MODE"] = mode
    os.environ["SUPERVISION_ENVIRONMENT"] = environment
    os.environ["SUPERVISION_JWT_SECRET"] = "k" * 48

    cfg = importlib.import_module("app.config")
    importlib.reload(cfg)
    sm = importlib.import_module("app.db.session")
    importlib.reload(sm)
    sm._engine = None
    sm._SessionLocal = None
    return sm


def tables_of(db_name: str) -> list[str]:
    engine = create_engine(f"{BASE_URL}/{db_name}", pool_pre_ping=True)
    try:
        return sorted(inspect(engine).get_table_names())
    finally:
        engine.dispose()


def main() -> int:
    failures = 0
    try:
        # ---------- 1) 全新库：alembic 自动建表 ----------
        print(f"[1] 全新库 {DB_NEW}（schema_mode=alembic）")
        recreate(DB_NEW)
        sm = load_app(DB_NEW, mode="alembic", environment="production")
        print(f"  resolve_schema_mode() = {sm.resolve_schema_mode()}")
        assert sm.init_db() is True, "init_db 返回 False"

        tables = tables_of(DB_NEW)
        business = [t for t in tables if t != "alembic_version"]
        print(f"  业务表 {len(business)} 张，alembic_version 存在: {'alembic_version' in tables}")
        if len(business) != 18:
            print(f"  [FAIL] 期望 18 张业务表，实际 {len(business)}")
            failures += 1
        else:
            print("  [OK] 18 张业务表已由迁移创建")
        if sm.alembic_has_version():
            print("  [OK] 已纳入版本管理")
        else:
            print("  [FAIL] 未记录迁移版本")
            failures += 1

        # 幂等：再次 init_db 不应报错
        sm.init_db()
        again = [t for t in tables_of(DB_NEW) if t != "alembic_version"]
        if len(again) == 18:
            print("  [OK] 重复 init_db 幂等（表数不变）")
        else:
            print(f"  [FAIL] 重复 init_db 后表数异常：{len(again)}")
            failures += 1

        # ---------- 2) 存量库：先由 create_all 建表，再 stamp 纳管 ----------
        print()
        print(f"[2] 存量库 {DB_LEGACY}（此前用 create_all 建表）")
        recreate(DB_LEGACY)
        # environment=dev + mode=create_all：模拟"升级前的老库"是由 create_all 建的
        sm2 = load_app(DB_LEGACY, mode="create_all", environment="dev")
        print(f"  resolve_schema_mode() = {sm2.resolve_schema_mode()}")
        sm2.init_db()

        legacy_tables = [t for t in tables_of(DB_LEGACY) if t != "alembic_version"]
        print(f"  create_all 建表 {len(legacy_tables)} 张")
        if len(legacy_tables) != 18:
            print(f"  [FAIL] create_all 应建 18 张，实际 {len(legacy_tables)}")
            failures += 1

        # 升级到新版本后切到 alembic 模式，此时旧库尚无 alembic_version，
        # 需先 stamp head 纳管（否则 upgrade 会尝试重复建表）
        sm2 = load_app(DB_LEGACY, mode="alembic", environment="production")
        print(f"  切换后 resolve_schema_mode() = {sm2.resolve_schema_mode()}")
        print(f"  纳管前 alembic_has_version() = {sm2.alembic_has_version()}")
        if sm2.alembic_has_version():
            print("  [FAIL] 旧库不应已有迁移版本记录")
            failures += 1

        command_ok = True
        try:
            sm2._alembic_config()
            from alembic import command

            command.stamp(sm2._alembic_config(), "head")
        except Exception as exc:  # noqa: BLE001
            command_ok = False
            print(f"  [FAIL] stamp 失败: {type(exc).__name__}: {str(exc)[:200]}")
            failures += 1

        if command_ok and sm2.alembic_has_version():
            print("  [OK] stamp 后已纳入版本管理")
        elif command_ok:
            print("  [FAIL] stamp 未生效")
            failures += 1

        # stamp 后 upgrade head 应为空操作，不得重复建表或报错
        if command_ok:
            from alembic import command

            command.upgrade(sm2._alembic_config(), "head")
            after = [t for t in tables_of(DB_LEGACY) if t != "alembic_version"]
            if len(after) == 18:
                print("  [OK] stamp 后 upgrade 为空操作，表结构不变")
            else:
                print(f"  [FAIL] stamp 后表数异常：{len(after)}")
                failures += 1
    finally:
        for name in (DB_NEW, DB_LEGACY):
            try:
                with admin().connect() as conn:
                    conn.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = :db AND pid <> pg_backend_pid()"
                        ),
                        {"db": name},
                    )
                    conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            except Exception as exc:  # noqa: BLE001
                print(f"  [WARN] 清理 {name} 失败: {type(exc).__name__}")
        print()
        print("临时库已清理")

    print()
    print("[OK] schema_mode 两条路径均验证通过" if not failures else f"[FAIL] {failures} 项未通过")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
