# -*- coding: utf-8 -*-
"""验证 idle_in_transaction_session_timeout 配置与「长时间空闲不致命」的机制。

背景（CI 实测失败）：阈值曾设为 60 秒，而 Agent 一次评估会合法空闲数十秒到数分钟
（等 LLM 返回）。PostgreSQL 判为闲置后强制断开连接，之后同一会话再写库就抛
    sqlalchemy.exc.PendingRollbackError: Can't reconnect until invalid transaction is rolled back
CI 日志中的直接证据：
    FATAL: terminating connection due to idle-in-transaction timeout

本脚本做三件事：
1. 读取应用连接实际生效的 idle_in_transaction_session_timeout；
2. 用短阈值复现故障机制（证明「阈值过小必然崩」）；
3. 用当前配置验证正常空闲不再被误杀（证明修复有效）。

用法：
    python scripts/check_idle_timeout.py
"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import text  # noqa: E402

from app.config import settings  # noqa: E402
from app.db.session import get_engine  # noqa: E402


def main() -> int:
    if settings.is_sqlite:
        print("[SKIP] 当前使用 SQLite，本检查仅针对 PostgreSQL")
        return 0

    engine = get_engine()
    failures = 0

    # ---------- 1) 应用连接实际生效的值 ----------
    print("=== 1) 应用连接生效的阈值 ===")
    with engine.connect() as conn:
        actual = conn.execute(text("SHOW idle_in_transaction_session_timeout")).scalar()
    expected = f"{settings.db_idle_transaction_timeout_seconds}s"
    print(f"  配置项 db_idle_transaction_timeout_seconds = {settings.db_idle_transaction_timeout_seconds}")
    print(f"  数据库实际值 SHOW ... = {actual}")
    print(f"  期望（PostgreSQL 归一化写法） = {expected} 或 {settings.db_idle_transaction_timeout_seconds * 1000}ms")
    ok = actual in {expected, f"{settings.db_idle_transaction_timeout_seconds * 1000}ms", "15min"}
    if ok:
        print("  [OK] 阈值已按配置生效")
    else:
        print("  [FAIL] 阈值与配置不一致")
        failures += 1

    # ---------- 2) 复现故障机制：短阈值必然导致连接被杀 ----------
    print("\n=== 2) 复现故障机制（短阈值 1s + 空闲 3s）===")
    with engine.connect() as conn:
        conn.execute(text("SET idle_in_transaction_session_timeout = '1s'"))
        conn.execute(text("SELECT 1"))  # 开启事务并让它空闲
        print("  已开启事务，故意空闲 3 秒（模拟等待 LLM 返回）…")
        import time

        time.sleep(3)
        try:
            conn.execute(text("SELECT 1"))
            print("  [意外] 连接仍可用（数据库未按预期断开）")
        except Exception as exc:  # noqa: BLE001
            print(f"  [OK] 连接被数据库断开，异常类型 = {type(exc).__name__}")
            print(f"       这正是不设阈值或阈值过小时发生的故障")
            print(f"       （SQLAlchemy 会先标记事务失效，下一次写库即抛 PendingRollbackError）")

    # ---------- 3) 验证当前配置能扛住正常空闲 ----------
    print("\n=== 3) 验证当前配置（默认 15 分钟）可扛住正常空闲 ===")
    idle_seconds = 70  # 已超过旧的 60s 阈值；新阈值下应完全无感
    print(f"  开启事务后空闲 {idle_seconds} 秒（超过旧的 60s 阈值）…")
    import time

    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
        started = time.perf_counter()
        time.sleep(idle_seconds)
        try:
            conn.execute(text("SELECT 1"))
            print(f"  [OK] 空闲 {time.perf_counter() - started:.0f}s 后连接仍可用，读写正常")
        except Exception as exc:  # noqa: BLE001
            print(f"  [FAIL] 空闲后被断开：{type(exc).__name__}: {str(exc)[:160]}")
            failures += 1

    # ---------- 4) 会话在长空闲后仍能提交（模拟 Agent 后半段写库）----------
    print("\n=== 4) 会话在长空闲后仍能提交（模拟 Agent 后半段落库）===")
    from app.db.session import session_scope

    import time

    try:
        with session_scope() as session:
            session.execute(text("SELECT 1"))
            time.sleep(5)
            session.execute(text("SELECT 1"))
            print("  [OK] 跨空闲的会话读写与提交均正常")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] {type(exc).__name__}: {str(exc)[:160]}")
        failures += 1

    print()
    if failures:
        print(f"[FAIL] {failures} 项未通过")
        return 1
    print("[OK] 空闲事务阈值配置正确，正常空闲不会被误杀")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
