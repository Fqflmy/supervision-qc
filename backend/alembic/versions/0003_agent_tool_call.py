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
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
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
    op.drop_index("ix_agent_tool_task", table_name="agent_tool_call")
    op.drop_table("agent_tool_call")
