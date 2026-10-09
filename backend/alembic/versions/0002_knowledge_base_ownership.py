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
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "knowledge_base",
        sa.Column("project_id", sa.BigInteger(), nullable=True),
    )
    op.add_column(
        "knowledge_base",
        sa.Column("owner_id", sa.BigInteger(), nullable=True),
    )
    op.create_foreign_key(
        "fk_knowledge_base_project",
        "knowledge_base",
        "project",
        ["project_id"],
        ["id"],
    )
    op.create_foreign_key(
        "fk_knowledge_base_owner",
        "knowledge_base",
        "sys_user",
        ["owner_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_knowledge_base_owner", "knowledge_base", type_="foreignkey")
    op.drop_constraint("fk_knowledge_base_project", "knowledge_base", type_="foreignkey")
    op.drop_column("knowledge_base", "owner_id")
    op.drop_column("knowledge_base", "project_id")
