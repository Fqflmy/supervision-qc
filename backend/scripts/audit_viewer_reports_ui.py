# -*- coding: utf-8 -*-
"""验证「普通用户可查看项目授权内的质量评估报告」。

覆盖：
1. 普通用户登录后落地「评估报告」页；
2. 报告列表显示授权项目内的报告（结论 / 风险 / 复核状态）；
3. 能进入报告详情阅读完整报告；
4. **看不到写操作入口**（重新执行 / 人工复核 / 判定合格等按钮不应出现）。

用法：python scripts/audit_viewer_reports_ui.py --base http://127.0.0.1:8080
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

#: 只读用户**不应**看到的写操作按钮文案
WRITE_ACTIONS = ["重新执行", "人工复核", "判定合格", "判定不合格", "新建评估任务", "提交并重新执行"]


def _login(page, base: str, card: str) -> None:
    page.goto(f"{base}/#/login", wait_until="networkidle", timeout=90000)
    page.evaluate("localStorage.clear()")
    page.goto(f"{base}/#/login", wait_until="networkidle", timeout=60000)
    page.wait_for_selector(".identity__card", timeout=30000)
    page.locator(".identity__card", has_text=card).first.click()
    page.wait_for_timeout(250)
    page.click('button:has-text("登 录")')
    page.wait_for_selector(".app-shell", timeout=30000)
    page.wait_for_timeout(2000)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "var" / "pw-browsers"))

    failures: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel="msedge")
        context = browser.new_context(viewport={"width": 1560, "height": 1000}, locale="zh-CN")
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)[:200]))

        print("=== 普通用户登录 ===")
        _login(page, base, "普通用户")
        landed = page.url.split("#")[-1] or "/"
        print(f"  落地页: {landed}")
        if landed.rstrip("/") != "/reports":
            failures.append(f"落地页应为 /reports，实际 {landed}")

        nav = [t.strip() for t in page.locator(".app-nav").inner_text().split("\n") if t.strip()]
        print(f"  侧边栏: {nav}")
        if "评估报告" not in nav:
            failures.append("侧边栏缺少「评估报告」")

        print()
        print("=== 评估报告列表 ===")
        page.goto(f"{base}/#/reports", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3500)

        tables = page.locator(".el-table")
        rows = tables.nth(0).locator(".el-table__row")
        headers = [
            t.strip() for t in tables.nth(0).locator("th .cell").all_inner_texts() if t.strip()
        ]
        print(f"  表头: {headers}")
        print(f"  行数: {rows.count()}")
        if rows.count() == 0:
            failures.append("报告列表为空（普通用户看不到授权项目内的报告）")
        else:
            values = [c.inner_text().strip() for c in rows.nth(0).locator("td").all()]
            print(f"  首行: {values}")
            for col in ("总体结论", "风险", "复核"):
                if col not in headers:
                    failures.append(f"表头缺少「{col}」")
                else:
                    v = values[headers.index(col)]
                    print(f"  「{col}」= {v!r}")
                    if v in ("", "-"):
                        failures.append(f"「{col}」为空")
        page.screenshot(path=str(ARTIFACTS / "22-viewer-reports.png"), full_page=True)

        print()
        print("=== 报告详情（只读）===")
        if rows.count() > 0:
            page.locator("button:has-text('查看报告')").first.click()
            page.wait_for_timeout(3500)
            body = page.inner_text("body")
            has_md = "评估报告" in body
            print(f"  进入详情页: {page.url.split('#')[-1]}")
            print(f"  含报告内容: {has_md}")
            if not has_md:
                failures.append("详情页未显示报告内容")

            leaked = [a for a in WRITE_ACTIONS if a in body]
            print(f"  泄漏的写操作按钮: {leaked if leaked else '无'}")
            if leaked:
                failures.append(f"只读用户看到了写操作入口：{leaked}")
            page.screenshot(path=str(ARTIFACTS / "22-viewer-report-detail.png"), full_page=True)

        browser.close()

    if errors:
        for line in errors[:5]:
            print(f"  页面错误: {line}")
        failures.extend(errors[:5])

    print()
    if failures:
        print(f"[FAIL] {len(failures)} 项未通过：")
        for f in failures:
            print(f"  {f}")
        return 1
    print("[OK] 普通用户可查看授权项目内的评估报告，且无写操作入口")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
