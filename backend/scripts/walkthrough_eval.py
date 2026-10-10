# -*- coding: utf-8 -*-
"""完整评估流程走查（浏览器实操，逐步截图）。

覆盖从**发起**到**签发**的全链路，每步验证界面的真实状态：

  1. 监理工程师登录 → 新建评估任务（浏览器填表）
  2. 执行评估（五阶段，真实 LLM）
  3. 查看报告（机器结论 + 条款匹配明细）
  4. 审核人员复核 → 判定合格并签发
  5. 抽查留痕（human_feedback / audit_log）

用法：
    python scripts/walkthrough_eval.py
    python scripts/walkthrough_eval.py --base http://127.0.0.1:8080
"""
from __future__ import annotations

import argparse
import os
import sys
import subprocess
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

ARTIFACTS = ROOT / "var" / "artifacts"
TITLE = "示例·地下室剪力墙混凝土浇筑质量评估"

#: 与 make_demo_task.py 一致的示例材料（第一条埋入入模温度超标问题）
RECORDS = [
    (
        "混凝土浇筑记录",
        "工程部位：地下室剪力墙（轴 3~7 / 标高 -6.20~-2.80）\n"
        "浇筑日期：2026-09-28  14:20—18:40\n"
        "强度等级：C35  方量：186 m³  坍落度：165 mm\n"
        "入模温度实测：35℃（当日气温 33℃，罐车未采取遮阳与降温措施）\n"
        "浇筑方式：泵送连续浇筑，分层厚度 500 mm",
    ),
    (
        "混凝土配合比通知单",
        "配合比编号：C35-P2026-0912\n"
        "水泥：P·O 42.5  粉煤灰：Ⅱ级  外加剂：聚羧酸高性能减水剂\n"
        "水胶比：0.44  砂率：41%  设计坍落度：160±30 mm",
    ),
    (
        "原材料复验报告",
        "水泥复验：安定性合格，3d 抗压强度 26.8 MPa，28d 抗压强度 48.2 MPa\n"
        "粉煤灰复验：细度 18.4%，需水量比 98%\n"
        "外加剂复验：减水率 26%，含气量 3.1%\n报告编号：FY-2026-0915",
    ),
    (
        "混凝土试块强度报告",
        "标养试块：3 组，28d 抗压强度 41.2 / 43.6 / 39.8 MPa（设计 C35，评定合格）\n"
        "同条件试块：2 组，用于结构实体检验\n报告编号：SJ-2026-0928",
    ),
    (
        "监理旁站记录",
        "旁站时间：2026-09-28 14:00—19:00\n旁站内容：混凝土浇筑全过程\n"
        "发现问题：入模温度偏高（实测 35℃），现场口头要求施工单位加强降温措施，"
        "但未形成书面整改记录。\n试块留置：标养 3 组、同条件 2 组",
    ),
]

steps: list[tuple[str, bool, str]] = []


def check(label: str, ok: bool, detail: str = "") -> bool:
    steps.append((label, ok, detail))
    print(f"  [{'OK' if ok else 'X '}] {label}{('  ' + detail) if detail else ''}")
    return ok


def _login(page, base: str, card: str) -> None:
    page.goto(f"{base}/#/login", wait_until="networkidle", timeout=90000)
    page.evaluate("localStorage.clear()")
    page.goto(f"{base}/#/login", wait_until="networkidle", timeout=60000)
    page.wait_for_selector(".identity__card", timeout=30000)
    page.locator(".identity__card", has_text=card).first.click()
    page.wait_for_timeout(250)
    page.click('button:has-text("登 录")')
    page.wait_for_selector(".app-shell", timeout=30000)
    page.wait_for_timeout(1500)


def db_query(code: str) -> str:
    """在全栈 api 容器内执行查询（避免连错库）。"""
    result = subprocess.run(
        ["docker", "exec", "supervision-api", "python", "-c", code],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180,
    )
    out = [ln for ln in (result.stdout or "").splitlines() if ln.strip()]
    return out[-1] if out else (result.stderr or "")[-200:]


def cleanup_previous() -> None:
    """清理上一次走查留下的任务，保证从头开始。"""
    code = (
        "import sys; sys.path.insert(0,'/app');"
        "from sqlalchemy import delete, select;"
        "from app.db.models import EvalTask, EvalReport, HumanFeedback, MatchResult,"
        "EvalSubtask, AgentStepLog, AgentToolCall, JudgeReview, JudgeScore;"
        "from app.db.session import session_scope;"
        f"title='{TITLE}';"
        "s=session_scope();db=s.__enter__();"
        "rows=db.execute(select(EvalTask).where(EvalTask.title==title)).scalars().all();"
        "ids=[t.id for t in rows];"
        "reps=db.execute(select(EvalReport).where(EvalReport.task_id.in_(ids))).scalars().all() if ids else [];"
        "rids=[r.id for r in reps];"
        "[db.execute(delete(m).where(m.report_id.in_(rids))) for m in (JudgeScore, JudgeReview)] if rids else None;"
        "[db.execute(delete(EvalReport).where(EvalReport.id.in_(rids)))] if rids else None;"
        "[db.execute(delete(m).where(m.task_id.in_(ids))) for m in "
        "(HumanFeedback, EvalSubtask, MatchResult, AgentStepLog, AgentToolCall)] if ids else None;"
        "[db.execute(delete(EvalTask).where(EvalTask.id.in_(ids)))] if ids else None;"
        "print(len(ids))"
    )
    removed = db_query(code)
    print(f"  已清理上次走查任务 {removed} 个")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "var" / "pw-browsers"))

    print("=== 准备：清理上次走查数据 ===")
    cleanup_previous()
    print()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel="msedge")
        context = browser.new_context(viewport={"width": 1560, "height": 1000}, locale="zh-CN")
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)[:200]))

        # ---------- 步骤 1：工程师登录并新建任务 ----------
        print("=== 步骤 1：监理工程师新建评估任务 ===")
        _login(page, base, "监理工程师")
        check("登录成功并进入评估任务页", "/evaluation" in page.url, page.url.split("#")[-1])

        page.click('button:has-text("新建评估任务")')
        page.wait_for_selector(".el-dialog", timeout=15000)
        page.wait_for_timeout(1200)
        dialog = page.locator(".el-dialog").last
        check("新建任务对话框已打开", dialog.is_visible())
        check("含「所属项目」且已自动选中", "示范工程" in dialog.inner_text(),
              "单项目自动归属")

        # 填表
        dialog.locator("input").first.fill(TITLE)

        def fill_by_label(label: str, value: str) -> None:
            item = dialog.locator(f'.el-form-item:has-text("{label}")').first
            item.locator("input, textarea").first.fill(value)

        fill_by_label("评估对象", "地下室剪力墙")
        fill_by_label("项目名称", "示范工程·某住宅小区 1# 楼")
        fill_by_label("问题描述", "检验批质量验收，核查混凝土施工过程控制与原材料资料完整性")

        # 待评材料：第一条已存在，填内容后逐条追加
        def fill_record(index: int, name: str, content: str) -> None:
            rows = dialog.locator(".record-row")
            while rows.count() <= index:
                dialog.locator('button:has-text("+ 添加材料")').click()
                page.wait_for_timeout(250)
                rows = dialog.locator(".record-row")
            row = rows.nth(index)
            inputs = row.locator("input, textarea")
            inputs.nth(0).fill(name)
            inputs.nth(1).fill(content)

        for i, (name, content) in enumerate(RECORDS):
            fill_record(i, name, content)
        page.wait_for_timeout(400)
        check(f"已填写 {len(RECORDS)} 条待评材料", dialog.locator(".record-row").count() == len(RECORDS))

        page.screenshot(path=str(ARTIFACTS / "19-step1-create-form.png"), full_page=True)

        dialog.locator('button:has-text("创建任务")').click()
        page.wait_for_timeout(4000)
        task_url = page.url
        task_id = task_url.rstrip("/").split("/")[-1]
        check("任务创建成功并跳转详情页", len(task_id) == 36, f"task_id={task_id[:8]}…")
        page.screenshot(path=str(ARTIFACTS / "19-step1-created.png"), full_page=True)

        # ---------- 步骤 2：执行评估 ----------
        print()
        print("=== 步骤 2：执行评估（五阶段，真实 LLM）===")
        run_btn = page.locator('button:has-text("执行评估"), button:has-text("重新执行")').first
        check("找到执行按钮", run_btn.count() > 0)
        run_btn.click()
        print("  已触发执行，等待 Agent 完成（约 30 秒）…")
        # 轮询任务状态直到离开 PENDING
        finished = False
        for _ in range(60):
            page.wait_for_timeout(3000)
            state = db_query(
                "import sys; sys.path.insert(0,'/app');import uuid;"
                "from app.db.models import EvalTask;from app.db.session import session_scope;"
                "s=session_scope();db=s.__enter__();"
                f"t=db.get(EvalTask, uuid.UUID('{task_id}'));print(t.current_state)"
            ).strip()
            if state not in ("PENDING", "PLANNING", "RETRIEVING", "MATCHING", "ANALYZING", "REPORTING", "JUDGING"):
                finished = True
                break
        check("Agent 执行完成", finished, f"最终状态={state}")
        check("负面结论已转人工复核", state == "NEED_HUMAN", f"状态={state}")

        page.wait_for_timeout(1500)
        page.reload(wait_until="networkidle")
        page.wait_for_timeout(2500)
        page.screenshot(path=str(ARTIFACTS / "19-step2-executed.png"), full_page=True)

        # ---------- 步骤 3：查看报告 ----------
        print()
        print("=== 步骤 3：查看评估报告 ===")
        report = db_query(
            "import sys; sys.path.insert(0,'/app');import uuid;"
            "from sqlalchemy import select;from app.db.models import EvalReport;"
            "from app.db.session import session_scope;"
            "s=session_scope();db=s.__enter__();"
            f"r=db.execute(select(EvalReport).where(EvalReport.task_id==uuid.UUID('{task_id}'))).scalar_one_or_none();"
            "print(f'{r.overall_verdict}|{r.risk_level}|{r.basis_count}|{r.non_compliance_count}|{r.human_verdict}|{r.is_final}')"
        )
        parts = report.split("|")
        check("报告已生成", len(parts) >= 4, report)
        if len(parts) >= 4:
            call("机器结论为「不符合」", parts[0] == "non_compliant", f"overall_verdict={parts[0]}")
            call("风险等级为「高」", parts[1] == "high", f"risk_level={parts[1]}")
            call("有条款依据", int(parts[2] or 0) > 0, f"依据条款数={parts[2]}")
            call("人工尚未裁定", parts[4] in ("None", ""), f"human_verdict={parts[4]}")

        # 关键条款命中
        hits = db_query(
            "import sys; sys.path.insert(0,'/app');import uuid;"
            "from sqlalchemy import select;from app.db.models import MatchResult;"
            "from app.db.session import session_scope;"
            "s=session_scope();db=s.__enter__();"
            f"rows=db.execute(select(MatchResult).where(MatchResult.task_id==uuid.UUID('{task_id}'))).scalars().all();"
            "print(','.join(f'{m.spec_code} {m.clause_no} {m.verdict}' for m in rows if m.verdict=='non_compliant'))"
        )
        call("命中埋设的问题条款（GB 50204-2015 5.3.3）", "5.3.3" in hits, hits or "未命中不符合条款")

        page.screenshot(path=str(ARTIFACTS / "19-step3-report.png"), full_page=True)

        # ---------- 步骤 4：审核人员签发 ----------
        print()
        print("=== 步骤 4：审核人员复核并签发 ===")
        _login(page, base, "审核人员")
        check("登录后落地质量评审页", "/judge" in page.url, page.url.split("#")[-1])

        queue = page.locator(".el-table__row")
        page.wait_for_timeout(2000)
        check("待复核队列中有该任务", queue.count() > 0, f"队列 {queue.count()} 行")
        page.screenshot(path=str(ARTIFACTS / "19-step4-queue.png"), full_page=True)

        # 进详情签发
        page.goto(f"{base}/#/evaluation/{task_id}", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)
        body = page.inner_text("body")
        call("详情页含「机器结论」栏", "机器结论" in body)
        call("详情页含「人工裁定」栏", "人工裁定" in body)
        call("显示「未签发」提示", "未签发" in body)

        page.locator('button:has-text("判定合格")').first.click()
        page.wait_for_selector(".el-dialog", timeout=15000)
        page.wait_for_timeout(800)
        page.locator(".el-dialog textarea").last.fill("已逐条核对引用条款与整改要求，同意 AI 结论，予以签发")
        page.locator('.el-dialog button:has-text("确认合格并签发")').last.click()
        page.wait_for_timeout(3500)

        signed = db_query(
            "import sys; sys.path.insert(0,'/app');import uuid;"
            "from sqlalchemy import select;from app.db.models import EvalReport;"
            "from app.db.session import session_scope;"
            "s=session_scope();db=s.__enter__();"
            f"r=db.execute(select(EvalReport).where(EvalReport.task_id==uuid.UUID('{task_id}'))).scalar_one_or_none();"
            "print(f'{r.human_verdict}|{r.is_final}|{r.reviewed_by}|{r.overall_verdict}')"
        )
        sp = signed.split("|")
        call("人工裁定已记录为合格", len(sp) > 0 and sp[0] == "qualified", signed)
        call("报告已签发生效", len(sp) > 1 and sp[1] == "True", signed)
        call("记录了签认人", len(sp) > 2 and sp[2] not in ("None", ""), f"reviewed_by={sp[2] if len(sp)>2 else '?'}")
        call("机器结论未被覆盖", len(sp) > 3 and sp[3] == "non_compliant",
              f"overall_verdict={sp[3] if len(sp)>3 else '?'}")
        call("界面显示「已签发」", "已签发" in page.inner_text("body"))
        page.screenshot(path=str(ARTIFACTS / "19-step4-signed.png"), full_page=True)

        # ---------- 步骤 5：留痕 ----------
        print()
        print("=== 步骤 5：抽查留痕 ===")
        audit = db_query(
            "import sys; sys.path.insert(0,'/app');import uuid;"
            "from sqlalchemy import select, func;from app.db.models import AuditLog, HumanFeedback;"
            "from app.db.session import session_scope;"
            "s=session_scope();db=s.__enter__();"
            f"tid=uuid.UUID('{task_id}');"
            "fb=db.execute(select(func.count()).select_from(HumanFeedback).where(HumanFeedback.task_id==tid)).scalar();"
            "al=db.execute(select(func.count()).select_from(AuditLog).where(AuditLog.action=='report_review')).scalar();"
            "print(f'{fb}|{al}')"
        )
        fp = audit.split("|")
        call("写入 human_feedback（迭代样本）", int(fp[0] or 0) >= 1, f"{fp[0]} 条")
        call("写入 audit_log（合规审计）", int(fp[1] or 0) >= 1, f"{fp[1]} 条")

        browser.close()

    if errors:
        print()
        print("=== 页面错误 ===")
        for line in errors[:5]:
            print(f"  {line}")

    ok_count = sum(1 for _, ok, _ in steps if ok)
    print()
    print("=" * 62)
    print(f"走查完成：通过 {ok_count} 项，失败 {len(steps) - ok_count} 项")
    print(f"task_id = {task_id}")
    print(f"截图目录 = {ARTIFACTS}")
    if ok_count != len(steps):
        print()
        for label, ok, detail in steps:
            if not ok:
                print(f"  失败：{label}  {detail}")
        return 1
    print("[OK] 全流程走通")
    return 0


def call(label: str, ok: bool, detail: str = "") -> None:
    check(label, ok, detail)


if __name__ == "__main__":
    raise SystemExit(main())
