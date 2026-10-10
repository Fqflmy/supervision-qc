# -*- coding: utf-8 -*-
"""sys_user 增加 must_change_password（管理员重置密码后要求改密）

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-10

背景
----
管理员的用户管理需要完整的增删改查，其中**重置密码**是不可缺的一项：
本系统没有自助找回密码能力（企业内网系统的常规做法是联系管理员），
因此用户忘记密码后只能由管理员重置。

但若管理员设置的临时密码被长期使用，等于把「管理员知情」变成了常态，
是明显的安全弱点。因此重置时置 ``must_change_password=True``，
要求用户下次登录后自行修改 —— 与「首次登录须改初始密码」的要求一致。

存量数据兼容
------------
默认 ``False``：不给现有账号强加改密要求，避免升级后所有用户被拦在改密页。
只有管理员**主动重置**某个密码时才会置 ``True``。

为什么写成「幂等」的
--------------------
开发环境（``db_schema_mode=create_all``）会按模型直接建表，该列可能
**已经被 create_all 建过**；直接 ADD COLUMN 会因列已存在而报错，
进而让容器反复重启（同类问题在 0002/0003/0004 上都实际踩到过）。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    if "must_change_password" not in _columns("sys_user"):
        op.add_column(
            "sys_user",
            sa.Column(
                "must_change_password",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )


def downgrade() -> None:
    if "must_change_password" in _columns("sys_user"):
        op.drop_column("sys_user", "must_change_password")
