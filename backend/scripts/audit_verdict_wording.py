# -*- coding: utf-8 -*-
"""复核结论术语防歧义验收（浏览器）。

用户反馈：报告中「复核结论：合格」无法判断是「AI 报告合格」还是「评估项目合格」。

本脚本断言界面上：
1. 复核决定显示为**动作词**（接受报告 / 退回报告），不是「合格/不合格」；
2. 每个结论都带**对象说明**（AI 判工程质量 / 人工判报告）；
3. 列表页两列（评估结论 / 报告复核）同时可见且列头写明对象；
4. 复核对话框说明「复核的是报告，不是工程质量」；
5. **关键场景**：AI 判「不符合」+ 人工「接受报告」时，界面不含任何
   暗示「工程质量合格」的表述。

用法：python scripts/audit_verdict_wording.py --base http://127.0.0.1:8080
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

#: 这些表述会让人以为是工程质量结论 —— 界面上不允许单独出现
AMBIGUOUS = [
    "复核结论",
    "裁定结论",
    "判定合格",
    "判定不合格",
    "直接落定不合格",
    "已复核不合格",
]

#: 必须出现的「对象说明」
REQUIRED_NOTES = [
    "AI 对工程质量的判定",
    "人工对 AI 报告的取舍",
]


def _login(page, base: str, card: str) -> None:
    page.goto(f"{base}/#/login", wait_until="networkidle", timeout=90000)
    page.evaluate("localStorage.clear()")
    page.goto(f"{base}/#/login", wait_until="networkidle", timeout=60000)
    page.wait_for_selector(".identity__card", timeout=30000)
    page.locator(".identity__card", has_text=card).first.click()
    page.wait_for_timeout(250)
    page.click('button:has-text("登 录")')
    page.wait_for_selector(".app-shell", timeout=30000)
    page.wait_for_timeout(1800)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "var" / "pw-browsers"))

    failures: list[str] = []
    checks = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal checks
        checks += 1
        print(f"  [{'OK' if ok else 'X '}] {label}{('  ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel="msedge")
        context = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)[:200]))

        # ---------- 1) 报告列表页 ----------
        print("=== 1) 评估报告列表 ===")
        _login(page, base, "普通用户")
        page.goto(f"{base}/#/reports", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        headers = page.locator(".el-table__header th").all_inner_texts()
        header_text = " | ".join(h.strip().replace("\n", " ") for h in headers)
        print(f"  列头: {header_text[:190]}")
        check("列头含「评估结论」", "评估结论" in header_text)
        check("列头含「报告复核」", "报告复核" in header_text)
        check("列头写明「AI 判工程质量」", "AI 判工程质量" in header_text)
        check("列头写明「人工判报告」", "人工判报告" in header_text)

        body = page.inner_text("body")
        leaked = [w for w in AMBIGUOUS if w in body]
        check("列表页无歧义表述", not leaked, str(leaked) if leaked else "")

        page.screenshot(path=str(ARTIFACTS / "27-wording-list.png"), full_page=True)

        # ---------- 2) 报告详情页 ----------
        print()
        print("=== 2) 报告详情（只读）===")
        # 必须选**已复核**的报告来验证「并存说明」：只有机器结论与人工决定都存在时，
        # 界面才需要解释「为何可以 不符合 + 接受报告」。
        # 若默认取第一行，可能落到待复核报告上，验证不到关键文案。
        rows = page.locator(".el-table__row")
        target_index = None
        for i in range(rows.count()):
            text = rows.nth(i).inner_text()
            if "已签发" in text or "已退回" in text:
                target_index = i
                break
        print(f"  已复核报告行: {target_index if target_index is not None else '未找到'}（共 {rows.count()} 行）")

        if rows.count() == 0:
            check("有报告可验证", False, "列表为空")
        else:
            row = rows.nth(target_index if target_index is not None else 0)
            row.locator("button:has-text('查看报告')").first.click()
            page.wait_for_timeout(3500)
            detail = page.inner_text("body")

            check("分区标题含「报告复核与签发」", "报告复核与签发" in detail)
            check(
                "含机器结论对象说明",
                "AI 对工程质量的判定" in detail,
                "「AI 对工程质量的判定」",
            )
            check(
                "含复核对象说明",
                "人工对 AI 报告的取舍" in detail,
                "「人工对 AI 报告的取舍」",
            )
            check("字段名是「复核决定」而非「复核结论」", "复核决定" in detail and "复核结论" not in detail)

            leaked_detail = [w for w in AMBIGUOUS if w in detail]
            check("详情页无歧义表述", not leaked_detail, str(leaked_detail) if leaked_detail else "")

            # 关键：是否有「判定对象不同」的解释
            check("解释了判定对象不同", "判定对象不同" in detail)

            # 关键场景：AI 判「不符合」时，界面不得暗示工程合格
            if "不符合" in detail:
                check(
                    "AI 判「不符合」时未出现「工程合格」类表述",
                    "工程质量合格" not in detail and "工程合格" not in detail,
                )

            page.screenshot(path=str(ARTIFACTS / "27-wording-detail.png"), full_page=True)

        # ---------- 3) 复核对话框（审核人员视角）----------
        print()
        print("=== 3) 复核对话框（审核人员）===")
        _login(page, base, "审核人员")
        page.goto(f"{base}/#/judge", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3500)
        judge_body = page.inner_text("body")

        leaked_judge = [w for w in AMBIGUOUS if w in judge_body]
        check("评审队列无歧义表述", not leaked_judge, str(leaked_judge) if leaked_judge else "")

        # 打开复核对话框（若有待复核项）
        btn = page.locator('button:has-text("接受并签发"), button:has-text("退回报告")')
        if btn.count() > 0:
            btn.first.click()
            page.wait_for_timeout(2000)
            dlg = page.locator(".el-dialog:visible")
            if dlg.count() > 0:
                dtext = dlg.last.inner_text()
                check("对话框说明复核对象是报告", "复核的是报告" in dtext)
                check("对话框说明不重新判定工程质量", "不是重新判定工程质量" in dtext)
                check(
                    "对话框含「复核意见」",
                    "复核意见" in dtext,
                    "而非「裁定依据」",
                )
                page.screenshot(path=str(ARTIFACTS / "27-wording-dialog.png"), full_page=True)
            else:
                check("复核对话框可打开", False)
        else:
            print("  [跳过] 队列无待复核项，无法验证对话框")

        browser.close()

    if errors:
        for line in errors[:5]:
            print(f"  页面错误: {line}")
        failures.extend(errors[:5])

    print()
    print("=" * 60)
    print(f"通过 {checks - len(failures)} / {checks} 项")
    if failures:
        for f in failures:
            print(f"  失败：{f}")
        return 1
    print("[OK] 复核结论术语已消歧：动作词 + 对象说明齐备，无歧义表述")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
