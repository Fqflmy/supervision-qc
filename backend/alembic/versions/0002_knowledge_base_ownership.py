# -*- coding: utf-8 -*-
"""knowledge_base 增加归属字段（project_id / owner_id）

Revision ID: 0002
Revises: 0001
Create Date: 2026-10-08

背景
----
修复越权漏洞时发现：``KnowledgeBase`` 没有归属字段，无法表达
「这个规范库属于哪个项目、由谁创建」，因此无法做知识库级授权。

本迁移新增两列：

- ``project_id``：归属项目。为空表示公共知识库（所有用户可读）；
  有值则仅该项目成员可访问（见 ``app/core/authz.py``）。
- ``owner_id``：创建者。让上传者始终可见自己建的知识库。

两列均可空，存量数据自动为 NULL（视为公共库 + 无创建者），
不会因升级而丢失可见性。

为什么写成「幂等」的
--------------------
开发环境（``db_schema_mode=create_all``）会按模型直接建表，
这两列可能**已经被 create_all 建过**。此时迁移若直接 ADD COLUMN，
会因为列已存在而报错，进而让容器启动失败。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def _foreign_keys(table: str) -> set[str]:
    return {fk["name"] for fk in inspect(op.get_bind()).get_foreign_keys(table) if fk.get("name")}


def upgrade() -> None:
    existing = _columns("knowledge_base")
    fks = _foreign_keys("knowledge_base")

    if "project_id" not in existing:
        op.add_column("knowledge_base", sa.Column("project_id", sa.BigInteger(), nullable=True))
    if "owner_id" not in existing:
        op.add_column("knowledge_base", sa.Column("owner_id", sa.BigInteger(), nullable=True))

    # 约束名在 create_all 建表时由数据库自动生成，因此只检查是否存在任何指向目标表的外键
    if "fk_knowledge_base_project" not in fks:
        op.create_foreign_key(
            "fk_knowledge_base_project",
            "knowledge_base",
            "project",
            ["project_id"],
            ["id"],
        )
    if "fk_knowledge_base_owner" not in fks:
        op.create_foreign_key(
            "fk_knowledge_base_owner",
            "knowledge_base",
            "sys_user",
            ["owner_id"],
            ["id"],
        )


def downgrade() -> None:
    existing = _columns("knowledge_base")
    if "owner_id" in existing:
        op.drop_column("knowledge_base", "owner_id")
    if "project_id" in existing:
        op.drop_column("knowledge_base", "project_id")
