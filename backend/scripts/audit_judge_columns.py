# -*- coding: utf-8 -*-
"""验证质量评审页三列（Judge 总分 / 等级 / 复核）是否显示数据。

对应缺陷：这三列此前恒为空 ——
- Judge 总分/等级：列表接口不返回 JudgeReview 数据（已加 with_judge 参数）；
- 复核：前端读 row.judge?.needs_human，而列表本就不含该字段（已改用 review_status）。

用法：python scripts/audit_judge_columns.py --base http://127.0.0.1:8080
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

        page.goto(f"{base}/#/login", wait_until="networkidle", timeout=90000)
        page.evaluate("localStorage.clear()")
        page.goto(f"{base}/#/login", wait_until="networkidle", timeout=60000)
        page.wait_for_selector(".identity__card", timeout=30000)
        page.locator(".identity__card", has_text="审核人员").first.click()
        page.wait_for_timeout(250)
        page.click('button:has-text("登 录")')
        page.wait_for_selector(".app-shell", timeout=30000)
        page.wait_for_timeout(2000)

        page.goto(f"{base}/#/judge", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        rows = page.locator(".el-table__row")
        print("=== 质量评审页「最近完成任务与评审结果」===")
        print(f"  页面表格行数: {rows.count()}")
        if rows.count() == 0:
            failures.append("表格无数据，无法验证列")
        else:
            # ⚠️ 页面上有**两个** el-table（待复核队列 + 评审结果），
            # 若用 page.locator("th") 会把两个表头拼在一起导致索引错位
            # （首版就因此误判「表头缺少 Judge 总分」，实际只是取了错误的表）。
            # 因此必须先把范围限定到含「Judge 总分」的那张表。
            tables = page.locator(".el-table")
            target_index = -1
            for i in range(tables.count()):
                headers_i = [
                    t.strip() for t in tables.nth(i).locator("th .cell").all_inner_texts()
                ]
                if "Judge 总分" in headers_i:
                    target_index = i
                    break
            if target_index < 0:
                failures.append("未找到含「Judge 总分」的表格")
            else:
                table = tables.nth(target_index)
                headers = [
                    t.strip() for t in table.locator("th .cell").all_inner_texts() if t.strip()
                ]
                first_row = table.locator(".el-table__row").first
                values = [c.inner_text().strip() for c in first_row.locator("td").all()]
                print(f"  表头: {headers}")
                print(f"  首行: {values}")

                def value_of(name: str) -> str | None:
                    if name not in headers:
                        return None
                    idx = headers.index(name)
                    return values[idx] if idx < len(values) else None

                for col in ("Judge 总分", "等级", "复核"):
                    v = value_of(col)
                    print(f"  「{col}」= {v!r}")
                    if v is None:
                        failures.append(f"表头缺少「{col}」")
                    elif v in ("", "-"):
                        failures.append(f"「{col}」仍为空（{v!r}）")

        page.screenshot(path=str(ARTIFACTS / "20-judge-columns.png"), full_page=True)
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
    print("[OK] 三列均已显示数据")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
