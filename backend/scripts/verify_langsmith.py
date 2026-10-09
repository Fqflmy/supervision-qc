# -*- coding: utf-8 -*-
"""验证 LangSmith 接入：Key 有效性 + 追踪数据确实上报。

分三步：
1. 用 LangSmith SDK 校验 API Key，并确认目标项目可见（能连到你的工作区）；
2. 在本地跑一个被 ``trace_span`` 覆盖的最小调用，确认追踪链路可用；
3. 等待并查询该项目下的 run，确认数据真的到了服务端。

只有第 3 步通过，才能说「接入成功」—— 前两步都可能在本地看起来正常，
但数据并未上报（例如项目名写错、网络不通、异步队列未 flush）。

用法：
    python scripts/verify_langsmith.py
    python scripts/verify_langsmith.py --wait 60
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--wait", type=int, default=45, help="上报后等待多少秒再查询")
    parser.add_argument("--project", default=None, help="覆盖项目名")
    args = parser.parse_args()

    from app.core.tracing import get_state, startup_message, trace_span

    state = get_state()
    print("=== 1) 追踪状态 ===")
    print(f"  {startup_message()}")
    print(f"  enabled={state.enabled} project={state.project} capture={state.capture_content}")
    if not state.enabled:
        print()
        print("[FAIL] 追踪未启用，无法验证。请检查 LANGSMITH_API_KEY / LANGSMITH_TRACING。")
        return 1

    project = args.project or state.project

    print()
    print("=== 2) 校验 API Key 与项目可见性 ===")
    try:
        from langsmith import Client

        client = Client()
        # list_projects 能反映 Key 是否有效（无效会抛认证错误）
        names = [p.name for p in client.list_projects(limit=50)]
        print(f"  认证成功；当前工作区可见项目 {len(names)} 个")
        if project in names:
            print(f"  目标项目已存在：{project}")
        else:
            print(f"  目标项目尚不存在（首次上报时会自动创建）：{project}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] 认证或查询失败：{type(exc).__name__}: {str(exc)[:300]}")
        return 1

    print()
    print("=== 3) 本地产生一条追踪并等待上报 ===")
    marker = f"verify-{int(time.time())}"
    try:
        with trace_span(
            "verify.langsmith",
            run_type="chain",
            inputs={"marker": marker, "purpose": "接入验证"},
            tags=["verify"],
        ) as span:
            time.sleep(0.2)
            span.outputs = {"ok": True, "marker": marker}
        print(f"  span 已结束，marker={marker}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] 产生追踪失败：{type(exc).__name__}: {str(exc)[:300]}")
        return 1

    print(f"  等待 {args.wait}s 让 SDK 把数据刷到服务端…")
    time.sleep(args.wait)

    print()
    print("=== 4) 从服务端查回这条追踪 ===")
    try:
        from langsmith import Client

        client = Client()
        found = None
        # 直接按项目列 run，匹配我们打的 marker
        for run in client.list_runs(project_name=project, limit=50):
            inputs = run.inputs or {}
            if inputs.get("marker") == marker:
                found = run
                break
        if found is None:
            print(f"  [FAIL] 在项目 {project} 中未找到 marker={marker} 的 run")
            print("         可能原因：项目名不一致、网络不通、或上报仍在队列中（可加大 --wait）")
            return 1
        print(f"  [OK] 已查回：id={found.id} name={found.name} run_type={found.run_type}")
        print(f"        outputs={found.outputs}")
    except Exception as exc:  # noqa: BLE001
        print(f"  [FAIL] 查询失败：{type(exc).__name__}: {str(exc)[:300]}")
        return 1

    print()
    print("[OK] LangSmith 接入验证通过：Key 有效、追踪数据已上报并可从服务端查回")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
