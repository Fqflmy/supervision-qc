# -*- coding: utf-8 -*-
"""Alembic 迁移环境配置。

设计要点
--------
1. **连接串不在 alembic.ini 里硬编码**，而是从应用配置（``SUPERVISION_DATABASE_URL``
   或 ``backend/.env``）读取。这样迁移与应用永远连同一个库，不会出现
   「迁移改了 A 库、应用连的是 B 库」这类事故，也避免密码写进版本库。
2. ``target_metadata`` 指向应用的 ``Base.metadata``，使 ``--autogenerate`` 可用。
3. 支持离线模式（生成 SQL 脚本，供 DBA 审核后执行）。

常用命令（在 backend/ 目录下执行）
----------------------------------
    python -m alembic current                 # 查看当前版本
    python -m alembic history --verbose       # 查看迁移历史
    python -m alembic upgrade head            # 升级到最新
    python -m alembic downgrade -1            # 回退一步
    python -m alembic revision --autogenerate -m "描述"   # 生成迁移
    python -m alembic upgrade head --sql      # 只输出 SQL 不执行

对已存在的库（此前由 create_all 建表）：
    python -m alembic stamp head              # 标记为已是最新，不再重复建表
"""
from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

# 允许以 `python -m alembic` 从 backend/ 目录运行时导入 app 包
BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.db import models  # noqa: E402  确保模型完成注册

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

#: 迁移的目标元数据
target_metadata = models.Base.metadata


def get_database_url() -> str:
    """从应用配置取连接串（而非 alembic.ini）。"""
    return settings.database_url


def run_migrations_offline() -> None:
    """离线模式：只生成 SQL，不连接数据库（供 DBA 审核后手工执行）。"""
    context.configure(
        url=get_database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """在线模式：直接连接数据库执行迁移。"""
    section = config.get_section(config.config_ini_section, {})
    # 用应用配置覆盖 ini 中的连接串
    section["sqlalchemy.url"] = get_database_url()

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            # 检测列类型与默认值变化，否则 autogenerate 会漏掉这些改动
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
