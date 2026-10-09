# -*- coding: utf-8 -*-
"""eval_report 增加人工复核裁定字段（human_verdict / review_comment）

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-09

背景
----
SRS 角色定义（第 92 行）要求质量评估专家「复核与裁定、修订评分、**终审签发**」；
业务流程（第 142 行）为「专家复核**签发** → 报告归档 → 全链路留痕」；
FR-AGT-11（P1）要求「报告签发」支持人工介入。

但实现中：

- ``reviewed_by`` / ``reviewed_at`` / ``is_final`` 三个字段**定义了却从未被写入**；
- **没有「人工裁定结论」字段** —— 记了「谁在何时复核」，却没记「结论是合格还是不合格」；
- 于是 ``is_final`` 永远是 ``False``，「正式报告」与「草稿」无法区分。

本迁移新增两列：

- ``human_verdict``：人工裁定结论（``qualified`` / ``unqualified``）。
  **与 ``overall_verdict``（机器结论）并存，不互相覆盖** ——
  机器结论是 AI 质量的原始证据（FR-JDG-06 反馈闭环与模型迭代对比要用），
  人工裁定是责任判定；两者不一致本身是重要样本。
- ``review_comment``：裁定依据/说明（与逐条条款修订说明分开记录）。

另为 ``eval_task`` 增加 ``version`` 乐观锁列：人工裁定提交时带期望版本，
防止两名审核人员同时裁定同一任务（不匹配返回 409）。
存量行默认 ``1``。

存量数据兼容
------------
上述列均可空/有默认值，存量报告自动为 NULL（语义「尚未经人工裁定」）。
**不要批量回填为 qualified** —— 那等于伪造签认记录。
前端对 NULL 显示「待复核」，导出时标注「未经人工复核」。

为什么写成「幂等」的
--------------------
开发环境（``db_schema_mode=create_all``）会按模型直接建表，这些列可能
**已经被 create_all 建过**。此时迁移若直接 ADD COLUMN 会因列已存在而报错，
进而让容器反复重启（同类问题在 0002/0003 上都实际踩到过）。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _columns(table: str) -> set[str]:
    return {c["name"] for c in inspect(op.get_bind()).get_columns(table)}


def upgrade() -> None:
    existing = _columns("eval_report")

    if "human_verdict" not in existing:
        op.add_column("eval_report", sa.Column("human_verdict", sa.String(16), nullable=True))
    if "review_comment" not in existing:
        op.add_column("eval_report", sa.Column("review_comment", sa.Text(), nullable=True))

    # 复核队列按状态筛选，人工裁定也有查询需求（如「列出所有已复核不合格」）。
    # 索引名固定，先检查是否存在再建。
    indexes = {i["name"] for i in inspect(op.get_bind()).get_indexes("eval_report")}
    if "ix_eval_report_human_verdict" not in indexes:
        op.create_index("ix_eval_report_human_verdict", "eval_report", ["human_verdict"])

    # 乐观锁版本列
    task_columns = _columns("eval_task")
    if "version" not in task_columns:
        op.add_column(
            "eval_task",
            sa.Column("version", sa.Integer(), nullable=False, server_default="1"),
        )


def downgrade() -> None:
    indexes = {i["name"] for i in inspect(op.get_bind()).get_indexes("eval_report")}
    if "ix_eval_report_human_verdict" in indexes:
        op.drop_index("ix_eval_report_human_verdict", table_name="eval_report")

    existing = _columns("eval_report")
    if "review_comment" in existing:
        op.drop_column("eval_report", "review_comment")
    if "human_verdict" in existing:
        op.drop_column("eval_report", "human_verdict")

    if "version" in _columns("eval_task"):
        op.drop_column("eval_task", "version")
