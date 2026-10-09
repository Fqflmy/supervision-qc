# -*- coding: utf-8 -*-
"""把数据库结构升级到最新版本（容器启动流程的第 1 步）。

为什么需要独立脚本
------------------
容器入口原先的顺序是：

    等待数据库 -> seed_data.py -> uvicorn（lifespan 里才建表/迁移）

而 ``seed_data.py`` 会读写**新增字段**（例如 ``knowledge_base.project_id``）。
对存量库（旧版本用 ``create_all`` 建的表）来说，迁移要到 uvicorn 启动后才执行，
于是种子必然先失败并打印「初始化失败」，服务却照常起来 ——
症状表现成「示例数据莫名其妙不见了」，排查方向很容易跑偏。

现在把「建表/迁移」提到种子之前显式执行：

    等待数据库 -> init_schema.py -> seed_data.py -> uvicorn

关于 schema_mode
----------------
容器内**始终走 alembic 迁移**，不看 ``db_schema_mode``：

- ``db_schema_mode`` 的 ``create_all`` 分支是为「本机开发时改模型不必写迁移」保留的，
  但它**不会补列**（这正是当初引入 alembic 的原因）；
- 容器是部署形态，表结构必须可追溯、可回滚，因此以迁移链为准；
- 全新库从这里从 0001 建到最新，之后 lifespan 的 create_all 变成空操作；
  存量库（有表但无 alembic_version）会先 stamp 基线再升级。

本脚本做的事与 ``app.db.session.migrate_to_latest`` 一致，且可重复执行（幂等）。
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.core.logging_conf import get_logger  # noqa: E402
from app.db.session import (  # noqa: E402
    alembic_has_version,
    existing_table_count,
    migrate_to_latest,
    ping,
    resolve_schema_mode,
)

logger = get_logger("scripts.init_schema")


def main() -> int:
    if not ping():
        print("[X] 数据库不可达，无法初始化结构", flush=True)
        return 1

    mode = resolve_schema_mode()
    tables = existing_table_count()
    versioned = alembic_has_version()
    print(
        f"[init_schema] 配置模式={mode}（容器内以迁移为准） 已有表={tables} 已纳管迁移={versioned}",
        flush=True,
    )

    try:
        migrate_to_latest()
    except Exception as exc:  # noqa: BLE001
        # 迁移失败必须让启动流程失败：带病启动会出现「代码期望新列、库里没有」的运行期故障
        print(f"[X] 结构迁移失败：{type(exc).__name__}: {str(exc)[:400]}", flush=True)
        logger.error("结构迁移失败", extra={"error": f"{type(exc).__name__}: {str(exc)[:400]}"})
        return 1

    print(
        f"[init_schema] 结构已就绪（表={existing_table_count()} 已纳管={alembic_has_version()}）",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
