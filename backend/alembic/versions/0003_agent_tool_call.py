# -*- coding: utf-8 -*-
"""新增 agent_tool_call 表（Agent 工具调用审计）

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-08

背景
----
补齐权限设计中的「工具调用权限」：Agent 的工具调用需要
工具级授权、参数校验与调用审计。

``agent_step_log`` 不适合承担审计：它是五阶段流程摘要，落库时会先删除重写
（用于断点续跑重放），因此不能作为不可变流水。本表专门记录每次工具调用。

为什么写成「幂等」的
--------------------
开发环境（``db_schema_mode=create_all``）会按模型直接建表，
因此本表可能**已经被 create_all 建过**。此时迁移若直接 CREATE TABLE，
会因为表已存在而报 DuplicateTable，进而让容器启动失败
（入口在结构迁移失败时会终止启动）—— 而这是从「本机 create_all 开发」
过渡到「迁移管理」时的常见路径。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _table_exists(name: str) -> bool:
    return name in set(inspect(op.get_bind()).get_table_names())


def _index_exists(table: str, index: str) -> bool:
    return any(idx["name"] == index for idx in inspect(op.get_bind()).get_indexes(table))


def upgrade() -> None:
    if _table_exists("agent_tool_call"):
        # 已由 create_all 建表：只补齐可能缺失的索引，不重复建表
        if not _index_exists("agent_tool_call", "ix_agent_tool_task"):
            op.create_index("ix_agent_tool_task", "agent_tool_call", ["task_id", "seq"])
        return

    op.create_table(
        "agent_tool_call",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("seq", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("tool", sa.String(length=64), nullable=False),
        sa.Column("allowed", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("user_id", sa.BigInteger(), nullable=True),
        sa.Column("params", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("output_digest", sa.String(length=255), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["task_id"], ["eval_task.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["sys_user.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_agent_tool_task", "agent_tool_call", ["task_id", "seq"])


def downgrade() -> None:
    if _table_exists("agent_tool_call"):
        op.drop_index("ix_agent_tool_task", table_name="agent_tool_call")
        op.drop_table("agent_tool_call")
