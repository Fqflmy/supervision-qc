# -*- coding: utf-8 -*-
"""人工复核界面验收（浏览器实操）。

1. 审核人员进入任务详情 → 应看到「机器结论 / 人工裁定」**分栏**，
   以及「判定合格」「判定不合格」按钮；
2. 点击「判定不合格」→ 对话框应要求填写裁定依据（前端拦截）；
3. 提交「判定合格」→ 状态徽标变为「已签发」，签认人与时间显示出来；
4. 质量评审页待复核队列 → 应有行内「判定合格 / 判定不合格」入口。

⚠️ 任务必须由 `seed_review_task.py` **在 api 容器内**创建后把 ID 传进来。
不要在本脚本里直接写库 —— 本机 5432 是 infra 数据库，
与全栈 api 使用的 `postgres:5432` 是**两个不同的库**，
在本机造的任务浏览器里根本看不到（这个坑实际踩到过）。

用法：
    # 1) 容器内造任务，拿到 ID
    TASK_ID=$(docker compose exec -T api python scripts/seed_review_task.py | tail -1)
    # 2) 宿主机跑浏览器验收
    python scripts/audit_review_ui.py --base http://127.0.0.1:8080 --task-id "$TASK_ID"
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

ARTIFACTS = ROOT / "var" / "artifacts"


def fetch_report_state(task_id: str):
    """从**全栈 api 容器**读取报告状态（经 docker exec，避免连错库）。"""
    import subprocess

    code = (
        "import sys; sys.path.insert(0,'/app');"
        "import uuid;from sqlalchemy import select;"
        "from app.db.models import EvalReport;from app.db.session import session_scope;"
        f"t=uuid.UUID('{task_id}');"
        "s=session_scope();db=s.__enter__();"
        "r=db.execute(select(EvalReport).where(EvalReport.task_id==t)).scalar_one_or_none();"
        "print('|'.join([str(r.human_verdict),str(r.is_final),str(r.reviewed_by),"
        "str(r.review_comment)[:40]] if r else ['NONE']))"
    )
    result = subprocess.run(
        ["docker", "exec", "supervision-api", "python", "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=120,
    )
    out = (result.stdout or "").strip().splitlines()
    return out[-1] if out else "NONE"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    parser.add_argument("--task-id", required=True, help="由 seed_review_task.py 在容器内创建")
    args = parser.parse_args()
    base = args.base.rstrip("/")
    task_id = args.task_id.strip()

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "var" / "pw-browsers"))

    failures: list[str] = []
    print(f"  待复核任务：{task_id[:8]}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel="msedge")
        context = browser.new_context(viewport={"width": 1560, "height": 1000}, locale="zh-CN")
        page = context.new_page()
        page_errors: list[str] = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)[:200]))

        # 以审核人员登录（该角色才有复核权限）
        page.goto(f"{base}/#/login", wait_until="networkidle", timeout=90000)
        page.evaluate("localStorage.clear()")
        page.goto(f"{base}/#/login", wait_until="networkidle", timeout=60000)
        page.wait_for_selector(".identity__card", timeout=30000)
        page.locator(".identity__card", has_text="审核人员").first.click()
        page.wait_for_timeout(250)
        page.click('button:has-text("登 录")')
        page.wait_for_selector(".app-shell", timeout=30000)
        page.wait_for_timeout(1500)

        # ---------- 1) 任务详情：机器/人工分栏 + 裁定按钮 ----------
        print()
        print("=== 审核人员 · 任务详情 ===")
        page.goto(f"{base}/#/evaluation/{task_id}", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)
        body = page.inner_text("body")

        has_machine = "机器结论" in body
        has_human = "人工裁定" in body
        has_qualified = page.locator('button:has-text("判定合格")').count() > 0
        has_unqualified = page.locator('button:has-text("判定不合格")').count() > 0
        has_final_warn = "未签发" in body

        print(f"  含「机器结论」栏     : {has_machine}")
        print(f"  含「人工裁定」栏     : {has_human}")
        print(f"  「判定合格」按钮     : {has_qualified}")
        print(f"  「判定不合格」按钮   : {has_unqualified}")
        print(f"  未签发提示           : {has_final_warn}")

        for ok, label in [
            (has_machine, "缺少「机器结论」栏"),
            (has_human, "缺少「人工裁定」栏"),
            (has_qualified, "缺少「判定合格」按钮"),
            (has_unqualified, "缺少「判定不合格」按钮"),
            (has_final_warn, "未显示「未签发」提示（无法区分草稿与正式件）"),
        ]:
            if not ok:
                failures.append(label)

        page.screenshot(path=str(ARTIFACTS / "18-review-panel.png"), full_page=True)

        # ---------- 2) 判定不合格必须填依据 ----------
        print()
        print("=== 判定不合格：必填依据校验 ===")
        if has_unqualified:
            page.locator('button:has-text("判定不合格")').first.click()
            page.wait_for_selector(".el-dialog", timeout=15000)
            page.wait_for_timeout(700)
            dialog_text = page.locator(".el-dialog").last.inner_text()
            print(f"  对话框含「裁定依据」: {'裁定依据' in dialog_text}")
            print(f"  含处置方式选择      : {'处置方式' in dialog_text}")

            if "裁定依据" not in dialog_text:
                failures.append("判定不合格对话框缺少「裁定依据」")
            if "处置方式" not in dialog_text:
                failures.append("判定不合格对话框缺少「处置方式」（驳回重跑/直接落定）")

            page.screenshot(path=str(ARTIFACTS / "18-review-unqualified-dialog.png"))

            page.locator('.el-dialog button:has-text("确认不合格")').last.click()
            page.wait_for_timeout(1200)
            still_open = page.locator(".el-dialog").count() > 0
            warn = page.locator(".el-message--warning").count() > 0
            print(f"  空依据提交被拦截    : 对话框仍开={still_open} 有警告={warn}")
            if not (still_open and warn):
                failures.append("判定不合格未填依据竟可提交（前端未拦截）")

            page.keyboard.press("Escape")
            page.wait_for_timeout(800)

        # ---------- 3) 判定合格 → 签发 ----------
        print()
        print("=== 判定合格 → 终审签发 ===")
        if has_qualified:
            page.locator('button:has-text("判定合格")').first.click()
            page.wait_for_selector(".el-dialog", timeout=15000)
            page.wait_for_timeout(700)
            page.locator(".el-dialog textarea").last.fill("界面验收：已核对引用条款，同意 AI 结论")
            page.wait_for_timeout(200)
            page.locator('.el-dialog button:has-text("确认合格并签发")').last.click()
            page.wait_for_timeout(3500)

            body2 = page.inner_text("body")
            signed = "已签发" in body2
            print(f"  界面显示「已签发」  : {signed}")
            if not signed:
                failures.append("判定合格后界面未显示「已签发」")
            page.screenshot(path=str(ARTIFACTS / "18-review-signed.png"), full_page=True)

            # 与数据库核对：界面不能只是自己说成功（经容器读，避免连错库）
            state = fetch_report_state(task_id)
            print(f"  库中 human_verdict|is_final|reviewed_by|comment : {state}")
            parts = state.split("|")
            if len(parts) < 3 or parts[0] != "qualified":
                failures.append(f"界面提交后库中 human_verdict 不是 qualified（{state}）")
            if len(parts) < 3 or parts[1] != "True":
                failures.append(f"界面提交后库中 is_final 不为 True（{state}）")
            if len(parts) < 3 or parts[2] in ("None", ""):
                failures.append(f"界面提交后库中未记录签认人（{state}）")

            # 机器结论必须保持原值（不被人工覆盖）
            body3 = page.inner_text("body")
            if "机器结论" in body3 and "合格" in body3:
                print("  机器结论仍展示      : True（未被人工裁定覆盖）")

        # ---------- 4) 待复核队列的行内裁定 ----------
        print()
        print("=== 质量评审 · 待复核队列 ===")
        page.goto(f"{base}/#/judge", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)
        judge_body = page.inner_text("body")
        queue_present = "待复核队列" in judge_body
        inline_ok = page.locator('button:has-text("判定合格")').count() > 0
        print(f"  含「待复核队列」     : {queue_present}")
        print(f"  队列含行内裁定按钮   : {inline_ok}")
        if not queue_present:
            failures.append("质量评审页缺少「待复核队列」")
        if not inline_ok:
            failures.append("待复核队列缺少行内裁定按钮")
        page.screenshot(path=str(ARTIFACTS / "18-judge-queue.png"), full_page=True)

        browser.close()

    if page_errors:
        print()
        print("=== 页面错误 ===")
        for line in page_errors[:5]:
            print(f"  {line}")
        failures.extend(page_errors[:5])

    print()
    if failures:
        print(f"[FAIL] {len(failures)} 项未通过：")
        for f in failures:
            print(f"  {f}")
        return 1
    print("[OK] 人工复核界面（分栏 / 必填依据 / 签发 / 队列行内裁定）全部符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

