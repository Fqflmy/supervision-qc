# -*- coding: utf-8 -*-
"""人工复核裁定与签发 —— 端到端验收（对真实服务执行）。

逐项验证整改方案第 2.2 节的 8 条验收标准：

1. 复核人能给出「合格」与「不合格」；
2. 判定后记录 human_verdict / reviewed_by / reviewed_at / is_final；
3. 判定合格 → is_final=true、任务 COMPLETED，可作正式依据；
4. 判定不合格 → rerun 决定驳回重跑（MATCHING）或直接落定（COMPLETED 但不签发）；
5. 非复核角色（engineer / viewer）返回 403；
6. 每次裁定写入 human_feedback 与 audit_log；
7. 人工裁定**不覆盖**机器结论（overall_verdict 保持原值）；
8. 未签发的报告有明确标识（review_status）。

用法（容器内）：
    docker compose exec api python scripts/verify_review_e2e.py http://127.0.0.1:8000
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
import uuid

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8000"
PASSWORD = "Admin@12345"

passed: list[str] = []
failed: list[str] = []


def call(method: str, path: str, token: str | None = None, payload: dict | None = None):
    url = f"{BASE}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except Exception:  # noqa: BLE001
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode()
        try:
            return exc.code, json.loads(raw)
        except Exception:  # noqa: BLE001
            return exc.code, raw
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)


def login(username: str) -> str | None:
    code, body = call("POST", "/api/v1/auth/login",
                      payload={"username": username, "password": PASSWORD})
    if code != 200:
        print(f"    [X] {username} 登录失败 {code}")
        return None
    return body["data"]["access_token"]


def check(label: str, ok: bool, detail: str = "") -> None:
    if ok:
        passed.append(label)
        print(f"  [OK] {label}{('  ' + detail) if detail else ''}")
    else:
        failed.append(f"{label}  {detail}")
        print(f"  [X ] {label}  {detail}")


def seed_task(state: str = "NEED_HUMAN") -> str:
    """在库中造一个「有报告 + 指定状态」的任务，返回 task_id。"""
    sys.path.insert(0, "/app")
    from app.db.models import EvalReport, EvalTask, Project, User
    from app.db.session import session_scope

    with session_scope() as session:
        project = session.query(Project).order_by(Project.id).first()
        owner = session.query(User).filter(User.username == "engineer").one()
        task = EvalTask(
            id=uuid.uuid4(),
            project_id=project.id,
            user_id=owner.id,
            title="验收·人工复核裁定",
            eval_type="inspection_lot",
            specialty="结构工程",
            current_state=state,
            iteration_count=2,
            version=1,
        )
        session.add(task)
        session.flush()
        session.add(
            EvalReport(
                id=uuid.uuid4(),
                task_id=task.id,
                conclusion="验收用报告",
                overall_verdict="qualified",  # 机器判「合格」
                risk_level="medium",
                summary="验收",
                content={"matches": []},
                basis_count=3,
                non_compliance_count=1,
                generator_model="deepseek-chat",
                human_verdict=None,
                is_final=False,
            )
        )
        return str(task.id)


def report_of(task_id: str):
    sys.path.insert(0, "/app")
    from sqlalchemy import select

    from app.db.models import EvalReport
    from app.db.session import session_scope

    with session_scope() as session:
        return session.execute(
            select(EvalReport).where(EvalReport.task_id == uuid.UUID(task_id))
        ).scalar_one_or_none()


def task_state(task_id: str) -> str:
    sys.path.insert(0, "/app")
    from app.db.models import EvalTask
    from app.db.session import session_scope

    with session_scope() as session:
        return session.get(EvalTask, uuid.UUID(task_id)).current_state


def main() -> int:
    print(f"=== 人工复核裁定与签发 · 端到端验收：{BASE} ===")
    print()

    tokens = {}
    print("=== 准备角色登录 ===")
    for role in ["admin", "engineer", "expert", "viewer"]:
        tokens[role] = login(role)
    if not all(tokens.values()):
        print("  [X] 有角色登录失败，终止")
        return 1
    print("  4 个角色登录成功")
    print()

    # ---------- 验收 1&3：判定合格 → 签发 ----------
    print("=== 验收 1/3：判定合格 → 终审签发 ===")
    task_id = seed_task("NEED_HUMAN")
    machine_before = report_of(task_id).overall_verdict
    print(f"  机器结论（判定前）: {machine_before}")

    code, body = call("POST", f"/api/v1/eval/tasks/{task_id}/review", tokens["expert"], {
        "verdict": "qualified",
        "comment": "已逐条核对引用条款，同意 AI 结论，予以签发",
    })
    ok = code == 200
    check("复核人可给出「合格」裁定", ok, f"code={code} {str(body)[:160] if not ok else ''}")
    if ok:
        d = body["data"]
        check("判定合格 → 签发生效 is_final", d["is_final"] is True)
        check("判定合格 → 任务置 COMPLETED", d["current_state"] == "COMPLETED")
        rep = report_of(task_id)
        check("记录签认人 reviewed_by", rep.reviewed_by is not None)
        check("记录签认时间 reviewed_at", rep.reviewed_at is not None)
        check("记录人工裁定 human_verdict", rep.human_verdict == "qualified")
        check("记录裁定依据 review_comment", bool(rep.review_comment))
        # ---------- 验收 7：不覆盖机器结论 ----------
        check(
            "人工裁定**未覆盖**机器结论（可对比）",
            rep.overall_verdict == machine_before,
            f"{machine_before} -> {rep.overall_verdict}",
        )

    # ---------- 验收 2&8：状态查询 ----------
    print()
    print("=== 验收 2/8：复核状态可查、未签发有标识 ===")
    stale_task = seed_task("NEED_HUMAN")
    code, body = call("GET", f"/api/v1/eval/tasks/{stale_task}/review", tokens["expert"])
    if code == 200:
        d = body["data"]
        check("未裁定报告 review_status=pending", d["review_status"] == "pending")
        check("未裁定报告 is_final=false（不得作为正式依据）", d["is_final"] is False)
        check("同时返回机器结论供对比", d["machine_verdict"] == "qualified")
    else:
        check("复核状态接口可用", False, f"code={code}")

    code, body = call("GET", f"/api/v1/eval/tasks/{task_id}/review", tokens["expert"])
    if code == 200:
        d = body["data"]
        check("已签发报告 review_status=signed", d["review_status"] == "signed")
        check("返回签认人姓名", bool(d.get("reviewed_by_name")), str(d.get("reviewed_by_name")))

    # ---------- 验收 4：判定不合格的两种处置 ----------
    print()
    print("=== 验收 4：判定不合格（驳回重跑 / 直接落定）===")
    t_rerun = seed_task("NEED_HUMAN")
    code, body = call("POST", f"/api/v1/eval/tasks/{t_rerun}/review", tokens["expert"], {
        "verdict": "unqualified",
        "comment": "第 3 条引用为已废止版本，且缺少材料复验记录",
        "rerun": True,
    })
    ok = code == 200
    check("判定「不合格」可提交", ok, f"code={code} {str(body)[:150] if not ok else ''}")
    if ok:
        check("不合格 → 不予签发", body["data"]["is_final"] is False)
        check("不合格 + rerun → 驳回重跑（MATCHING）",
              body["data"]["current_state"] == "MATCHING",
              f"实际 {body['data']['current_state']}")

    t_final = seed_task("NEED_HUMAN")
    code, body = call("POST", f"/api/v1/eval/tasks/{t_final}/review", tokens["expert"], {
        "verdict": "unqualified",
        "comment": "材料确实不足，重跑无意义",
        "rerun": False,
    })
    if code == 200:
        check("不合格 + 不重跑 → 直接落定（COMPLETED）",
              body["data"]["current_state"] == "COMPLETED")
        check("直接落定后仍不签发", report_of(t_final).is_final is False)

    # 必填校验
    code, _ = call("POST", f"/api/v1/eval/tasks/{seed_task()}/review", tokens["expert"], {
        "verdict": "unqualified", "comment": "", "rerun": False,
    })
    check("判定不合格未填依据被拒", code in (400, 422), f"code={code}")

    # 枚举校验
    code, _ = call("POST", f"/api/v1/eval/tasks/{seed_task()}/review", tokens["expert"], {
        "verdict": "pass", "comment": "自由字符串应被拒",
    })
    check("非法 verdict 被拒（枚举校验）", code == 422, f"code={code}")

    # ---------- 验收 5：权限 ----------
    print()
    print("=== 验收 5：非复核角色不得裁定 ===")
    for role in ["engineer", "viewer"]:
        code, _ = call("POST", f"/api/v1/eval/tasks/{seed_task()}/review", tokens[role], {
            "verdict": "qualified", "comment": "越权尝试",
        })
        check(f"{role} 裁定被拒（403）", code == 403, f"code={code}")

    # ---------- 验收 6：留痕 ----------
    print()
    print("=== 验收 6：留痕（human_feedback + audit_log）===")
    trace_task = seed_task("NEED_HUMAN")
    call("POST", f"/api/v1/eval/tasks/{trace_task}/review", tokens["expert"], {
        "verdict": "unqualified",
        "comment": "人工判不合格而机器判合格 —— 分歧样本",
        "rerun": False,
    })
    sys.path.insert(0, "/app")
    from sqlalchemy import select

    from app.db.models import AuditLog, HumanFeedback
    from app.db.session import session_scope

    with session_scope() as session:
        feedbacks = session.execute(
            select(HumanFeedback).where(HumanFeedback.task_id == uuid.UUID(trace_task))
        ).scalars().all()
        check("写入 human_feedback（FR-JDG-06 样本）", len(feedbacks) >= 1, f"{len(feedbacks)} 条")
        check("样本 action 记录裁定结论",
              any(f.action == "unqualified" for f in feedbacks),
              str([f.action for f in feedbacks]))

        logs = session.execute(
            select(AuditLog).where(AuditLog.action == "report_review")
        ).scalars().all()
        check("写入 audit_log（FR-SYS-03 审计）", len(logs) >= 1, f"{len(logs)} 条")
        diverged = any((l.detail or {}).get("diverged") for l in logs)
        check("人工与机器分歧被标记（可作迭代样本）", diverged)

    # ---------- 验收 8：待复核队列口径 ----------
    print()
    print("=== 验收 8：待复核队列（尚无人工裁定）===")
    q_task = seed_task("NEED_HUMAN")
    code, body = call("GET", "/api/v1/eval/reviews/pending?page_size=100", tokens["expert"])
    check("待复核队列可访问", code == 200, f"code={code}")
    if code == 200:
        ids = [i["task_id"] for i in body["data"]["items"]]
        check("未裁定任务在队列中", q_task in ids)
        call("POST", f"/api/v1/eval/tasks/{q_task}/review", tokens["expert"], {
            "verdict": "qualified", "comment": "处理完毕",
        })
        code, body = call("GET", "/api/v1/eval/reviews/pending?page_size=100", tokens["expert"])
        ids2 = [i["task_id"] for i in body["data"]["items"]]
        check("已裁定任务移出队列", q_task not in ids2)

    code, _ = call("GET", "/api/v1/eval/reviews/pending", tokens["viewer"])
    check("非复核角色不能读待复核队列", code == 403, f"code={code}")

    # ---------- 乐观锁 ----------
    print()
    print("=== 附加：乐观锁（防并发改判）===")
    v_task = seed_task("NEED_HUMAN")
    code, _ = call("POST", f"/api/v1/eval/tasks/{v_task}/review", tokens["expert"], {
        "verdict": "qualified", "comment": "版本过期应冲突", "expected_version": 999,
    })
    check("过期版本触发 409 冲突", code == 409, f"code={code}")

    print()
    print("=" * 64)
    print(f"通过 {len(passed)} 项，失败 {len(failed)} 项")
    if failed:
        print()
        print("失败明细：")
        for item in failed:
            print(f"  - {item}")
        return 1
    print("[OK] 人工复核裁定与签发全部验收通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
