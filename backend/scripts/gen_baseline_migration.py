# -*- coding: utf-8 -*-
"""生成 Alembic 基线迁移（离线，不需要运行中的数据库）。

背景
----
常规做法 `alembic revision --autogenerate` 需要连接数据库反射现有结构。
数据库不可用时无法使用，因此这里用 Alembic 的 ``produce_migrations`` API：
把「空库」与应用的 ``Base.metadata`` 做差异比较，直接生成建表迁移。
对首次迁移而言，结果与 autogenerate 等价。

关于自定义列类型
----------------
模型里的 ``JSONType`` / ``GUID`` 是 ``TypeDecorator``，Alembic 会把它们渲染成
``app.db.models.JSONType()`` —— 迁移文件里没有该导入会直接 NameError。
它们在不同方言下落到不同实现（PostgreSQL 为 JSONB 与 UUID），因此基线迁移中
直接写标准类型更清晰、也让迁移文件自包含（不依赖应用代码演进）。

用法：
    python scripts/gen_baseline_migration.py
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from alembic.autogenerate import produce_migrations, render_python_code  # noqa: E402
from alembic.migration import MigrationContext  # noqa: E402
from alembic.operations import Operations  # noqa: E402
from sqlalchemy import create_engine, pool  # noqa: E402
from sqlalchemy.dialects.postgresql import JSONB, UUID as PGUUID  # noqa: E402

from app.db import models  # noqa: E402

REVISION = "0001"
SLUG = "baseline_schema"
VERSIONS_DIR = BACKEND / "alembic" / "versions"

#: 自定义 TypeDecorator -> 迁移中使用的标准类型写法
TYPE_REWRITES = {
    "app.db.models.JSONType()": "postgresql.JSONB(astext_type=sa.Text())",
    "app.db.models.GUID(length=36)": "postgresql.UUID(as_uuid=True)",
    "app.db.models.GUID()": "postgresql.UUID(as_uuid=True)",
}


def rewrite_types(code: str) -> tuple[str, int]:
    """把自定义类型替换为标准类型，返回 (新代码, 替换次数)。"""
    count = 0
    for old, new in TYPE_REWRITES.items():
        count += code.count(old)
        code = code.replace(old, new)
    return code, count


def main() -> int:
    VERSIONS_DIR.mkdir(parents=True, exist_ok=True)

    engine = create_engine("sqlite://", poolclass=pool.StaticPool)
    with engine.connect() as connection:
        context = MigrationContext.configure(
            connection,
            opts={"compare_type": True, "compare_server_default": True},
        )
        operations = Operations(context)
        script = produce_migrations(context, models.Base.metadata)
        upgrade_code = render_python_code(
            script.upgrade_ops,
            migration_context=context,
            render_as_batch=False,
            sqlalchemy_module_prefix="sa.",
            alembic_module_prefix="op.",
        )

    table_count = len(models.Base.metadata.tables)
    upgrade_code, rewritten = rewrite_types(upgrade_code)

    created = upgrade_code.count("op.create_table(")
    print(f"  模型表数: {table_count}")
    print(f"  生成 create_table: {created} 条")
    print(f"  自定义类型改写: {rewritten} 处")
    if created < table_count:
        print(f"  [WARN] 生成语句少于表数: {created} < {table_count}")
        return 1
    if "app.db.models" in upgrade_code:
        print("  [FAIL] 仍残留 app.db.models 引用，迁移会 NameError")
        return 1

    body = "\n".join(
        f"    {line}" if line.strip() else "" for line in upgrade_code.rstrip().splitlines()
    )
    drop_lines = "\n".join(
        f'    op.drop_table("{name}")' for name in reversed(list(models.Base.metadata.tables))
    )

    content = f'''# -*- coding: utf-8 -*-
"""{SLUG}

Revision ID: {REVISION}
Revises:
Create Date: 2026-10-01

基线迁移：创建全部业务表（{table_count} 张）。

说明
----
1. 本迁移由 ``scripts/gen_baseline_migration.py`` 离线生成，
   内容等价于对空库执行 ``Base.metadata.create_all()``，
   并已通过 ``scripts/verify_migration_parity.py`` 逐项校验一致。
2. 模型中的 ``JSONType`` / ``GUID`` 是 TypeDecorator，此处直接写标准类型
   （JSONB / UUID），使迁移文件自包含、不依赖应用代码演进。
3. 对「已由 create_all 建表」的存量数据库，**不要执行本迁移**，而应标记为已应用：

       python -m alembic stamp head

   此后所有结构变更都通过新迁移进行，不再使用 create_all。
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "{REVISION}"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
{body}


def downgrade() -> None:
    """回退：按依赖倒序删除全部表。"""
{drop_lines}
'''

    target = VERSIONS_DIR / f"{REVISION}_{SLUG}.py"
    target.write_text(content, encoding="utf-8", newline="\n")
    print(f"  已写入 {target.relative_to(BACKEND)}（{len(content.splitlines())} 行）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
