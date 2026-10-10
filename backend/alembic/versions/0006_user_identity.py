# -*- coding: utf-8 -*-
"""sys_user 增加人员身份绑定字段（工号 / 单位 / 部门 / 岗位 / 执业证号 / 签认署名）

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-10

背景
----
账号原先只有 ``username`` + ``full_name`` + 角色，缺少「这个账号对应现实中的谁」。

对监理系统来说这不是锦上添花：**评估报告必须签认到具体的人**
（监理规范要求质量责任到人，报告中要体现单位、岗位、执业证号）。
只有登录 ID（如 ``eng_zhao``）无法满足这一要求，也无法与人事/项目台账对齐。

新增字段
--------
- ``employee_no``：工号（单位内唯一）
- ``org_name``：所属单位（建设/监理/施工单位）
- ``department``：所属部门
- ``position``：职务/岗位
- ``cert_no``：执业资格证号
- ``signature``：签认署名（留空时展示回退到 ``full_name``）

**账号与身份刻意分离**：``username`` / ``password_hash`` / ``role`` 属于凭据与权限；
上述字段属于人员身份。混在一起会导致「改姓名要动登录凭据」这类耦合问题。

存量数据兼容
------------
全部可空，存量账号不受影响。工号唯一性通过**部分索引**约束
（``WHERE employee_no IS NOT NULL``）—— 普通 UNIQUE 在 PG 与 SQLite 上
都不会把多个 NULL 视为冲突，但显式部分索引意图更清晰且可跨库一致。

为什么写成「幂等」的
--------------------
开发环境（``db_schema_mode=create_all``）会按模型直接建表，这些列可能
**已经被 create_all 建过**；直接 ADD COLUMN 会因列已存在而报错并让容器反复重启
（同类问题在 0002~0005 上都实际踩到过）。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

#: 新增列：(列名, 长度)
NEW_COLUMNS: tuple[tuple[str, int], ...] = (
    ("employee_no", 64),
    ("org_name", 128),
    ("department", 128),
    ("position", 64),
    ("cert_no", 64),
    ("signature", 64),
)


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    existing = _columns("sys_user")
    for name, length in NEW_COLUMNS:
        if name not in existing:
            op.add_column("sys_user", sa.Column(name, sa.String(length), nullable=True))

    # 工号唯一（允许为空，且空值不参与唯一性）
    indexes = {i["name"] for i in inspect(op.get_bind()).get_indexes("sys_user")}
    uniques = {u["name"] for u in inspect(op.get_bind()).get_unique_constraints("sys_user")}
    if "uq_sys_user_employee_no" not in indexes and "uq_sys_user_employee_no" not in uniques:
        op.create_index(
            "uq_sys_user_employee_no",
            "sys_user",
            ["employee_no"],
            unique=True,
            postgresql_where=sa.text("employee_no IS NOT NULL"),
            sqlite_where=sa.text("employee_no IS NOT NULL"),
        )


def downgrade() -> None:
    indexes = {i["name"] for i in inspect(op.get_bind()).get_indexes("sys_user")}
    if "uq_sys_user_employee_no" in indexes:
        op.drop_index("uq_sys_user_employee_no", table_name="sys_user")

    existing = _columns("sys_user")
    for name, _length in reversed(NEW_COLUMNS):
        if name in existing:
            op.drop_column("sys_user", name)
