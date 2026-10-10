# -*- coding: utf-8 -*-
"""验证评估任务列表/详情页的「进度」列显示是否合理。

缺陷背景：进度是状态派生的离散值，`NEED_HUMAN` 被赋 0.9。
任务签发后状态变 COMPLETED 但 progress 未同步，界面显示「90%」+
「已完成」徽标 —— 看起来像卡住了。

修复后：
- 数据层：progress 由 current_state 派生（不再读库中存储值），不可能漂移；
- 展示层：只在**执行中**状态显示进度条，已停止状态显示「等待人工复核」等说明。

用法：python scripts/audit_progress_display.py --base http://127.0.0.1:8080
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

#: 这些状态下**不应**出现进度条（Agent 已停止）
NOT_IN_FLIGHT = {"NEED_HUMAN", "DEGRADED", "COMPLETED", "FAILED", "CANCELLED"}


def _login(page, base: str, card: str = "监理工程师") -> None:
    page.goto(f"{base}/#/login", wait_until="networkidle", timeout=90000)
    page.evaluate("localStorage.clear()")
    page.goto(f"{base}/#/login", wait_until="networkidle", timeout=60000)
    page.wait_for_selector(".identity__card", timeout=30000)
    page.locator(".identity__card", has_text=card).first.click()
    page.wait_for_timeout(250)
    page.click('button:has-text("登 录")')
    page.wait_for_selector(".app-shell", timeout=30000)
    page.wait_for_timeout(1500)


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

        # ---------- 列表页 ----------
        print("=== 评估任务列表 · 进度列 ===")
        _login(page, base, "监理工程师")
        page.goto(f"{base}/#/evaluation", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)

        tables = page.locator(".el-table")
        headers = [
            t.strip() for t in tables.nth(0).locator("th .cell").all_inner_texts() if t.strip()
        ]
        print(f"  表头: {headers}")
        rows = tables.nth(0).locator(".el-table__row")
        print(f"  行数: {rows.count()}")

        if rows.count() == 0:
            failures.append("列表无数据，无法验证")
        else:
            for i in range(rows.count()):
                values = [c.inner_text().strip() for c in rows.nth(i).locator("td").all()]
                state = values[headers.index("状态")] if "状态" in headers else "?"
                prog = values[headers.index("进度")] if "进度" in headers else "?"
                print(f"  状态={state!r:12s} 进度列={prog!r}")
                # 已停止状态不应显示百分比
                if state in ("已完成", "待人工复核", "降级完成", "失败", "已取消"):
                    if prog.endswith("%"):
                        failures.append(
                            f"状态「{state}」仍显示百分比进度 {prog}（应显示状态说明）"
                        )

        page.screenshot(path=str(ARTIFACTS / "21-progress-list.png"), full_page=True)

        # ---------- 详情页 ----------
        print()
        print("=== 任务详情 · 进度字段 ===")
        if rows.count() > 0:
            rows.nth(0).locator("button:has-text('详情')").click()
            page.wait_for_timeout(2500)
            body = page.inner_text("body")
            has_wait = "等待人工复核" in body
            has_done = "已完成" in body
            print(f"  含「等待人工复核」: {has_wait}")
            print(f"  含「已完成」      : {has_done}")
            if not (has_wait or has_done):
                failures.append("详情页未显示状态说明文字（可能仍在显示百分比）")
            page.screenshot(path=str(ARTIFACTS / "21-progress-detail.png"), full_page=True)

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
    print("[OK] 进度显示符合预期（已停止状态不再显示百分比）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
