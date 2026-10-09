# -*- coding: utf-8 -*-
"""生成「评估示例」任务 —— **经 API 创建**，确保 input_payload 结构与后端约定一致。

⚠️ 为什么不直接写库
------------------
``create_task`` 里 ``input_payload = body.object.model_dump()`` —— 是**扁平结构**
（part / project_name / description / records 直接在顶层）。
若直接入库并写成嵌套的 ``{"object": {...}}``，Agent 读不到材料，
会报「未提交材料、证据不足」，看起来像系统故障，实际是造数据的方式错了。
（这个坑实际踩到过，所以本脚本改为调 API。）

同时经 API 创建也顺带验证了「工程师不传 project_id 也能建任务」这条修复。

用法（容器内）：
    python scripts/make_demo_task.py            # 创建（幂等）并打印 task_id
    python scripts/make_demo_task.py --cleanup  # 清理示例数据
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

BASE = "http://127.0.0.1:8000"
TITLE = "示例·地下室剪力墙混凝土浇筑质量评估"

#: 示例材料 —— 第一条**故意埋入超温问题**（入模温度 35℃ > 限值 30℃），
#: 使 Agent 能检出实质性不符合项，从而走到人工复核环节。
RECORDS = [
    {
        "name": "混凝土浇筑记录",
        "content": (
            "工程部位：地下室剪力墙（轴 3~7 / 标高 -6.20~-2.80）\n"
            "浇筑日期：2026-09-28  14:20—18:40\n"
            "强度等级：C35  方量：186 m³  坍落度：165 mm\n"
            "入模温度实测：35℃（当日气温 33℃，罐车未采取遮阳与降温措施）\n"
            "浇筑方式：泵送连续浇筑，分层厚度 500 mm"
        ),
    },
    {
        "name": "混凝土配合比通知单",
        "content": (
            "配合比编号：C35-P2026-0912\n"
            "水泥：P·O 42.5  粉煤灰：Ⅱ级  外加剂：聚羧酸高性能减水剂\n"
            "水胶比：0.44  砂率：41%  设计坍落度：160±30 mm"
        ),
    },
    {
        "name": "原材料复验报告",
        "content": (
            "水泥复验：安定性合格，3d 抗压强度 26.8 MPa，28d 抗压强度 48.2 MPa\n"
            "粉煤灰复验：细度 18.4%，需水量比 98%\n"
            "外加剂复验：减水率 26%，含气量 3.1%\n"
            "报告编号：FY-2026-0915"
        ),
    },
    {
        "name": "混凝土试块强度报告",
        "content": (
            "标养试块：3 组，28d 抗压强度 41.2 / 43.6 / 39.8 MPa（设计 C35，评定合格）\n"
            "同条件试块：2 组，用于结构实体检验\n"
            "报告编号：SJ-2026-0928"
        ),
    },
    {
        "name": "监理旁站记录",
        "content": (
            "旁站时间：2026-09-28 14:00—19:00\n"
            "旁站内容：混凝土浇筑全过程\n"
            "发现问题：入模温度偏高（实测 35℃），现场口头要求施工单位加强降温措施，"
            "但未形成书面整改记录。\n"
            "试块留置：标养 3 组、同条件 2 组"
        ),
    },
]


def call(method: str, path: str, token: str | None = None, body: dict | None = None):
    data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, json.loads(resp.read().decode())
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return exc.code, raw
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def login(username: str, password: str = "Admin@12345") -> str | None:
    code, body = call("POST", "/api/v1/auth/login",
                      body={"username": username, "password": password})
    return body["data"]["access_token"] if code == 200 else None


def find_existing() -> str | None:
    """找出已有的示例任务（幂等，避免重复创建）。"""
    token = login("engineer")
    if not token:
        return None
    code, body = call("GET", "/api/v1/eval/tasks?page_size=100", token)
    if code != 200:
        return None
    for item in body["data"]["items"]:
        if item.get("title") == TITLE:
            return item["id"]
    return None


def create_task() -> str:
    existing = find_existing()
    if existing:
        print(f"  示例任务已存在，复用：{existing[:8]}")
        return existing

    token = login("engineer")
    if token is None:
        raise RuntimeError("engineer 登录失败，请确认服务已启动且种子数据已写入")

    payload = {
        "eval_type": "inspection_lot",
        "specialty": "结构工程",
        "title": TITLE,
        # 注意：不传 project_id —— 工程师只被授权一个项目时应自动归属
        "object": {
            "part": "地下室剪力墙",
            "project_name": "示范工程·某住宅小区 1# 楼",
            "description": "检验批质量验收，核查混凝土施工过程控制与原材料资料完整性",
            "records": RECORDS,
        },
        "options": {"max_iterations": 12, "no_progress_limit": 2},
    }
    code, body = call("POST", "/api/v1/eval/tasks", token, payload)
    if code not in (200, 201):
        raise RuntimeError(f"创建示例任务失败：{code} {str(body)[:300]}")
    return body["data"]["task_id"]


def cleanup() -> int:
    from sqlalchemy import delete, select

    from app.db.models import (
        AgentStepLog,
        AgentToolCall,
        EvalReport,
        EvalSubtask,
        EvalTask,
        HumanFeedback,
        MatchResult,
    )
    from app.db.session import session_scope

    with session_scope() as session:
        rows = session.execute(select(EvalTask).where(EvalTask.title == TITLE)).scalars().all()
        ids = [t.id for t in rows]
        if not ids:
            print("  没有示例任务需要清理")
            return 0
        reports = session.execute(
            select(EvalReport).where(EvalReport.task_id.in_(ids))
        ).scalars().all()
        if reports:
            session.execute(delete(EvalReport).where(EvalReport.id.in_([r.id for r in reports])))
        for model in (HumanFeedback, EvalSubtask, MatchResult, AgentStepLog, AgentToolCall):
            session.execute(delete(model).where(model.task_id.in_(ids)))
        session.execute(delete(EvalTask).where(EvalTask.id.in_(ids)))
        print(f"  已清理示例任务 {len(ids)} 个")
        return len(ids)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cleanup", action="store_true")
    args = parser.parse_args()

    if args.cleanup:
        cleanup()
        return 0

    task_id = create_task()
    print(task_id)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
