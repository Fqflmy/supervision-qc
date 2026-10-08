# -*- coding: utf-8 -*-
"""验证 Alembic 迁移与 create_all 产出的表结构完全一致。

为什么要这个验证
----------------
项目此前用 ``Base.metadata.create_all()`` 建表，现在新增 Alembic 迁移。
若两者产出的结构存在任何差异，就会出现「开发环境正常、迁移上线后报错」
这类最难排查的问题。因此必须逐项对比。

验证内容
--------
1. 建两个空库；
2. 前者用 ``alembic upgrade head`` 建表，后者用 ``create_all`` 建表；
3. 逐表对比 列 / 类型 / 可空 / 主键 / 索引 / 唯一约束 / 外键；
4. 验证 ``alembic downgrade base`` 能干净回退；
5. 清理临时库。

用法（需数据层已启动）：
    python scripts/verify_migration_parity.py
"""
from __future__ import annotations

import importlib
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from alembic import command  # noqa: E402
from alembic.config import Config as AlembicConfig  # noqa: E402
from sqlalchemy import create_engine, inspect, text  # noqa: E402
from sqlalchemy.engine import Engine  # noqa: E402

from app.config import settings  # noqa: E402
from app.db import models  # noqa: E402

DB_MIG = "supervision_mig"
DB_CA = "supervision_ca"


# --------------------------------------------------------------------------- #
# 连接与建库
# --------------------------------------------------------------------------- #
def url_with_db(name: str) -> str:
    """把当前连接串的库名替换为 name。"""
    return settings.database_url.rsplit("/", 1)[0] + f"/{name}"


def admin_engine() -> Engine:
    """连到同实例的 postgres 库（用于 CREATE/DROP DATABASE）。

    不用 postgres 超级用户：compose 只创建了业务用户 supervision，
    且未给 postgres 设密码。该业务用户具备 CREATEDB 权限（已验证）。
    """
    return create_engine(url_with_db("postgres"), isolation_level="AUTOCOMMIT", pool_pre_ping=True)


def recreate_database(name: str) -> None:
    with admin_engine().connect() as conn:
        conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
        conn.execute(text(f'CREATE DATABASE "{name}"'))


def alembic_config() -> AlembicConfig:
    cfg = AlembicConfig(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "alembic"))
    return cfg


def run_migration(action: str, version: str, target_url: str) -> None:
    """执行迁移。

    ``env.py`` 通过 ``app.config.settings`` 取连接串，因此这里改环境变量后
    必须清缓存并重建 settings，才能让 env.py 连到目标库。
    """
    os.environ["SUPERVISION_DATABASE_URL"] = target_url
    config_module = importlib.import_module("app.config")
    session_module = importlib.import_module("app.db.session")
    config_module.get_settings.cache_clear()
    config_module.settings = config_module.get_settings()
    session_module._engine = None
    session_module._SessionLocal = None

    if action == "upgrade":
        command.upgrade(alembic_config(), version)
    else:
        command.downgrade(alembic_config(), version)


# --------------------------------------------------------------------------- #
# 结构快照与对比
# --------------------------------------------------------------------------- #
def snapshot(engine: Engine) -> dict:
    inspector = inspect(engine)
    result: dict = {}
    for table in sorted(inspector.get_table_names()):
        if table == "alembic_version":
            continue
        columns = {
            col["name"]: {
                "type": str(col["type"]),
                "nullable": bool(col.get("nullable", True)),
            }
            for col in inspector.get_columns(table)
        }
        pk = inspector.get_pk_constraint(table)
        result[table] = {
            "columns": columns,
            "pk": tuple(pk.get("constrained_columns") or []),
            "indexes": sorted(
                (idx.get("name") or "", tuple(idx.get("column_names") or []))
                for idx in inspector.get_indexes(table)
            ),
            "uniques": sorted(
                (uc.get("name") or "", tuple(uc.get("column_names") or []))
                for uc in inspector.get_unique_constraints(table)
            ),
            "fks": sorted(
                (fk.get("referred_table") or "", tuple(fk.get("constrained_columns") or []))
                for fk in inspector.get_foreign_keys(table)
            ),
        }
    return result


def diff(a: dict, b: dict, label_a: str, label_b: str) -> list[str]:
    problems: list[str] = []
    only_a, only_b = sorted(set(a) - set(b)), sorted(set(b) - set(a))
    if only_a:
        problems.append(f"仅在 {label_a} 存在: {only_a}")
    if only_b:
        problems.append(f"仅在 {label_b} 存在: {only_b}")

    for table in sorted(set(a) & set(b)):
        ca, cb = a[table], b[table]
        for name in sorted(set(ca["columns"]) | set(cb["columns"])):
            va, vb = ca["columns"].get(name), cb["columns"].get(name)
            if va != vb:
                problems.append(f"  {table}.{name}: {label_a}={va} vs {label_b}={vb}")
        for key in ("pk", "indexes", "uniques", "fks"):
            if ca[key] != cb[key]:
                problems.append(f"  {table}.{key}: {label_a}={ca[key]} vs {label_b}={cb[key]}")
    return problems


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main() -> int:
    print(f"数据库实例: {settings.database_url.rsplit('@', 1)[-1]}")
    print(f"模型表数: {len(models.Base.metadata.tables)}")
    print()

    problems: list[str] = []
    engines: list[Engine] = []

    def make_engine(url: str) -> Engine:
        engine = create_engine(url, pool_pre_ping=True)
        engines.append(engine)
        return engine

    try:
        # ---------- 1) 迁移路径 ----------
        print(f"[1] alembic upgrade head -> {DB_MIG}")
        recreate_database(DB_MIG)
        mig_url = url_with_db(DB_MIG)
        try:
            run_migration("upgrade", "head", mig_url)
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] 迁移执行失败: {type(exc).__name__}: {str(exc)[:400]}")
            return 1
        print("  迁移执行完成")

        # ---------- 2) create_all 路径 ----------
        print(f"[2] create_all -> {DB_CA}")
        recreate_database(DB_CA)
        ca_engine = make_engine(url_with_db(DB_CA))
        models.Base.metadata.create_all(bind=ca_engine)
        print("  create_all 完成")

        # ---------- 3) 对比 ----------
        print("[3] 对比结构")
        mig_snap = snapshot(make_engine(mig_url))
        ca_snap = snapshot(ca_engine)
        print(f"  迁移路径 {len(mig_snap)} 张表 / create_all 路径 {len(ca_snap)} 张表")
        problems = diff(mig_snap, ca_snap, "迁移", "create_all")
        print()
        if problems:
            print(f"  [FAIL] {len(problems)} 处差异：")
            for line in problems[:40]:
                print(f"    {line}")
        else:
            print("  [OK] 完全一致（表/列/类型/可空/主键/索引/唯一/外键）")

        # ---------- 4) 回退 ----------
        print()
        print("[4] alembic downgrade base")
        try:
            run_migration("downgrade", "base", mig_url)
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] 回退失败: {type(exc).__name__}: {str(exc)[:400]}")
            return 1
        leftover = [
            t for t in inspect(make_engine(mig_url)).get_table_names()
            if t != "alembic_version"
        ]
        if leftover:
            print(f"  [FAIL] 回退后残留表: {leftover[:8]}")
            problems.append(f"downgrade 残留 {len(leftover)} 张表")
        else:
            print("  [OK] 业务表已全部删除（仅剩 alembic_version）")
    finally:
        # 释放所有连接后再删库，否则 PostgreSQL 会报 ObjectInUse
        for engine in engines:
            engine.dispose()
        try:
            with admin_engine().connect() as conn:
                for name in (DB_MIG, DB_CA):
                    conn.execute(
                        text(
                            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                            "WHERE datname = :db AND pid <> pg_backend_pid()"
                        ),
                        {"db": name},
                    )
                    conn.execute(text(f'DROP DATABASE IF EXISTS "{name}"'))
            print()
            print("临时库已清理")
        except Exception as exc:  # noqa: BLE001
            print(f"  [WARN] 清理临时库失败（不影响结论）: {type(exc).__name__}")

    print()
    print("[OK] 迁移与 create_all 结构一致，且可回退" if not problems else f"[FAIL] {len(problems)} 处问题")
    return 0 if not problems else 1


if __name__ == "__main__":
    raise SystemExit(main())
