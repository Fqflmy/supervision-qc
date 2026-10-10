# -*- coding: utf-8 -*-
"""企业化界面完善验收：面包屑导航 + 只读报告详情页。

背景
----
1. **面包屑**：企业系统标配，让用户明确「我在哪、上一层是什么」。
   原先从列表点进详情后，只能靠浏览器后退返回。
2. **只读报告详情**：原先普通用户点「查看报告」进入的是**任务详情**
   （含 Token 消耗、迭代次数、执行轨迹、写操作按钮）—— 对只读用户既无用也不该看。
   现改为独立只读页，只呈现报告与签发状态。

用法：python scripts/audit_enterprise_ui.py --base http://127.0.0.1:8080
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

#: 只读用户不应在报告详情里看到的「运维/写操作」内容
FORBIDDEN_IN_READONLY = [
    "重新执行",
    "人工复核",
    "判定合格",
    "判定不合格",
    "Token 消耗",
    "迭代次数",
    "无进展轮次",
    "Agent 执行轨迹",
    "触发质量评审",
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
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel="msedge")
        context = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)[:200]))

        # ---------- 1) 面包屑 ----------
        print("=== 1) 面包屑导航 ===")
        _login(page, base, "普通用户")

        for path, expect_any in [
            ("reports", ["评估报告"]),
            ("chat", ["智能问答"]),
            # 知识库页对**只读角色**的菜单名是「规范查询」（有 kb:write 才是「知识库管理」）。
            # 面包屑与侧边栏用词必须一致，因此这里接受任一 —— 关键是「不为空且与菜单一致」。
            ("knowledge", ["规范查询", "知识库"]),
        ]:
            page.goto(f"{base}/#/{path}", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(1800)
            crumbs = [
                t.strip()
                for t in page.locator(".app-breadcrumb .el-breadcrumb__inner").all_inner_texts()
                if t.strip()
            ]
            nav_items = [
                t.strip()
                for t in page.locator(".app-nav").inner_text().split("\n")
                if t.strip()
            ]
            print(f"  /{path:10s} 面包屑={crumbs}")
            if not crumbs:
                failures.append(f"/{path} 未渲染面包屑")
            elif not any(e in " / ".join(crumbs) for e in expect_any):
                failures.append(f"/{path} 面包屑内容不符：{crumbs}")
            # 面包屑应与侧边栏用词一致（否则用户会以为是两个地方）
            elif crumbs[0] not in nav_items:
                failures.append(
                    f"/{path} 面包屑「{crumbs[0]}」与侧边栏菜单 {nav_items} 用词不一致"
                )

        # ---------- 2) 只读报告详情 ----------
        print()
        print("=== 2) 只读报告详情 ===")
        page.goto(f"{base}/#/reports", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        rows = page.locator(".el-table__row")
        if rows.count() == 0:
            failures.append("报告列表为空，无法验证详情页")
            print("  [X] 报告列表为空")
        else:
            page.locator("button:has-text('查看报告')").first.click()
            page.wait_for_timeout(3500)

            cur = (page.url.split("#")[-1] or "/").rstrip("/")
            print(f"  跳转到: {cur}")
            if not cur.startswith("/reports/"):
                failures.append(f"「查看报告」未跳到只读详情页（实际 {cur}）")

            body = page.inner_text("body")
            crumbs = [
                t.strip()
                for t in page.locator(".app-breadcrumb .el-breadcrumb__inner").all_inner_texts()
                if t.strip()
            ]
            print(f"  面包屑: {crumbs}")
            if len(crumbs) < 2:
                failures.append(f"详情页面包屑缺少上级层级：{crumbs}")

            # 必须有的只读要素
            for need in ["评估结论", "复核与签发", "报告正文"]:
                ok = need in body
                print(f"  [{'OK' if ok else 'X '}] 含「{need}」")
                if not ok:
                    failures.append(f"报告详情缺少「{need}」")

            # 不应出现的运维/写操作内容
            leaked = [k for k in FORBIDDEN_IN_READONLY if k in body]
            print(f"  泄漏的运维/写操作内容: {leaked if leaked else '无'}")
            if leaked:
                failures.append(f"只读报告详情泄漏了运维/写操作内容：{leaked}")

            page.screenshot(path=str(ARTIFACTS / "25-report-detail-readonly.png"), full_page=True)

        # ---------- 3) 面包屑可点击返回上级 ----------
        print()
        print("=== 3) 面包屑可点击返回 ===")
        link = page.locator(".app-breadcrumb__link")
        if link.count() > 0:
            link.first.click()
            page.wait_for_timeout(2000)
            back = (page.url.split("#")[-1] or "/").rstrip("/")
            print(f"  点击后面包屑后到达: {back}")
            if back != "/reports":
                failures.append(f"面包屑返回未到列表页（实际 {back}）")
        else:
            failures.append("详情页面包屑缺少可点击的上级链接")

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
    print("[OK] 面包屑导航与只读报告详情均符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
