# -*- coding: utf-8 -*-
"""查明并清理卡死的数据库会话，完成 checkpoints 索引建立。

背景：AsyncPostgresSaver.setup() 会执行
    CREATE INDEX CONCURRENTLY IF NOT EXISTS checkpoints_thread_id_idx ...
而 CREATE INDEX CONCURRENTLY 必须等待所有并发事务结束。若另有连接
处于 `idle in transaction` 且持有该表锁，就会永久互等（锁死）。

用法：
    docker compose cp backend/scripts/fix_checkpoint_locks.py api:/app/scripts/
    docker compose exec -T api python -u /app/scripts/fix_checkpoint_locks.py            # 只诊断
    docker compose exec -T api python -u /app/scripts/fix_checkpoint_locks.py --fix      # 清理并修复
"""
from __future__ import annotations

import argparse
import sys
import time

sys.path.insert(0, "/app")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fix", action="store_true", help="终止卡死会话并完成索引创建")
    args = parser.parse_args()

    import psycopg

    from app.agent.runner import _psycopg_dsn

    with psycopg.connect(_psycopg_dsn(), autocommit=True, connect_timeout=8) as conn:
        with conn.cursor() as cur:
            print("=== 阻塞中的会话 ===")
            cur.execute(
                """
                SELECT pid, state, wait_event_type, wait_event,
                       now() - xact_start AS xact_age,
                       left(regexp_replace(coalesce(query, ''), '\\s+', ' ', 'g'), 70) AS q
                FROM pg_stat_activity
                WHERE datname = current_database()
                  AND pid <> pg_backend_pid()
                  AND (state = 'idle in transaction' OR wait_event_type = 'Lock')
                ORDER BY xact_start NULLS LAST
                """
            )
            rows = cur.fetchall()
            for pid, state, wev_type, wev, age, q in rows:
                print(f"  pid={pid} state={state} wait={wev_type}/{wev} xact_age={age}\n      {q}")

            print("\n=== checkpoints 上的锁 ===")
            cur.execute(
                """
                SELECT l.pid, l.mode, l.granted, a.state,
                       now() - a.xact_start AS xact_age
                FROM pg_locks l JOIN pg_stat_activity a ON a.pid = l.pid
                WHERE l.relation = 'checkpoints'::regclass AND l.pid <> pg_backend_pid()
                """
            )
            for pid, mode, granted, state, age in cur.fetchall():
                print(f"  pid={pid} mode={mode} granted={granted} state={state} xact_age={age}")

            print("\n=== 索引是否存在 ===")
            cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'checkpoints'")
            print("  ", [r[0] for r in cur.fetchall()])

            if not args.fix:
                print("\n提示：加 --fix 终止卡死会话并创建索引")
                return 0

            victims = [
                pid
                for pid, state, wev_type, _wev, _age, _q in rows
                if state == "idle in transaction" or wev_type == "Lock"
            ]
            print(f"\n=== 终止 {len(victims)} 个卡死会话 ===")
            for pid in victims:
                try:
                    cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
                    print(f"  已终止 pid={pid}")
                except Exception as exc:  # noqa: BLE001
                    print(f"  终止 pid={pid} 失败：{exc}")

            print("\n=== 建立缺失索引（非并发，避免再次锁死）===")
            cur.execute(
                "CREATE INDEX IF NOT EXISTS checkpoints_thread_id_idx ON checkpoints(thread_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS checkpoint_blobs_thread_id_idx ON checkpoint_blobs(thread_id)"
            )
            cur.execute(
                "CREATE INDEX IF NOT EXISTS checkpoint_writes_thread_id_idx ON checkpoint_writes(thread_id)"
            )
            cur.execute("SELECT indexname FROM pg_indexes WHERE tablename = 'checkpoints'")
            print("  索引：", [r[0] for r in cur.fetchall()])

    # 验证 setup() 现在能快速返回
    print("\n=== 验证 AsyncPostgresSaver.setup() ===")
    import asyncio

    async def verify() -> None:
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from psycopg_pool import AsyncConnectionPool

        pool = AsyncConnectionPool(
            conninfo=_psycopg_dsn(), min_size=1, max_size=4,
            kwargs={"autocommit": True, "prepare_threshold": 0}, open=False,
        )
        await pool.open(wait=True, timeout=15)
        saver = AsyncPostgresSaver(pool)
        started = time.perf_counter()
        try:
            await asyncio.wait_for(saver.setup(), timeout=60)
            print(f"  [OK] setup 完成，耗时 {time.perf_counter() - started:.2f}s")
        except asyncio.TimeoutError:
            print("  [!!] setup 仍超时")
        except Exception as exc:  # noqa: BLE001
            print(f"  [X ] setup 异常：{type(exc).__name__}: {str(exc)[:200]}")
        await pool.close()

    asyncio.run(verify())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
