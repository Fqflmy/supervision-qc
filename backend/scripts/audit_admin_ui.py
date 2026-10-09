# -*- coding: utf-8 -*-
"""核实管理员界面是否具备「用户管理」与「知识库管理」两项功能。

用浏览器真实操作，不只看菜单是否存在：
1. 侧边栏是否含这两项；
2. 点进去页面是否真正渲染出功能（表格 / 按钮），而不是空白或报错；
3. 关键操作入口是否存在（新建用户、上传规范）。

用法：
    python scripts/audit_admin_ui.py
    python scripts/audit_admin_ui.py --base http://127.0.0.1:8080
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
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = context.new_page()
        page_errors: list[str] = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)[:200]))

        page.goto(f"{base}/#/login", wait_until="networkidle", timeout=90000)
        page.wait_for_selector(".identity__card", timeout=30000)
        page.locator(".identity__card", has_text="系统管理员").first.click()
        page.wait_for_timeout(250)
        page.click('button:has-text("登 录")')
        page.wait_for_selector(".app-shell", timeout=30000)
        page.wait_for_timeout(1500)

        nav = page.locator(".app-nav").inner_text()
        print("=== 管理员侧边栏 ===")
        for item in [t.strip() for t in nav.split("\n") if t.strip()]:
            print(f"  · {item}")

        for required in ("用户与授权", "知识库管理"):
            ok = required in nav
            if not ok:
                failures.append(f"侧边栏缺少「{required}」")
            print(f"  [{'OK' if ok else 'X '}] 含「{required}」")
        print()

        # ---------- 用户管理 ----------
        print("=== 用户与授权页 ===")
        page.goto(f"{base}/#/users", wait_until="networkidle", timeout=60000)
        page.wait_for_selector("text=用户与项目授权", timeout=30000)
        page.wait_for_timeout(1200)
        rows = page.locator(".el-table__row").count()
        has_create = page.locator("text=新建用户").count() > 0
        has_grant = page.locator("text=项目授权").count() > 0
        print(f"  用户表格行数     : {rows}")
        print(f"  「新建用户」入口 : {'有' if has_create else '无'}")
        print(f"  「项目授权」入口 : {'有' if has_grant else '无'}")
        if rows == 0:
            failures.append("用户列表为空")
        if not has_create:
            failures.append("缺少「新建用户」入口")
        # 打开新建对话框，确认表单可用
        if has_create:
            page.locator("text=新建用户").first.click()
            page.wait_for_selector("text=授权项目", timeout=15000)
            page.wait_for_timeout(600)
            dialog_ok = page.locator(".el-dialog").count() > 0
            print(f"  新建用户对话框   : {'可打开' if dialog_ok else '打不开'}")
            if not dialog_ok:
                failures.append("新建用户对话框打不开")
            page.screenshot(path=str(ARTIFACTS / "16-admin-users.png"))
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)
        print()

        # ---------- 知识库管理 ----------
        print("=== 知识库管理页 ===")
        page.goto(f"{base}/#/knowledge", wait_until="networkidle", timeout=60000)
        page.wait_for_selector("text=知识库管理", timeout=30000)
        page.wait_for_timeout(1500)
        kb_rows = page.locator(".el-table__row").count()
        body = page.inner_text("body")
        # 管理功能标志：上传入口与解析/发布类操作
        has_upload = ("上传" in body) or ("导入" in body)
        print(f"  文档表格行数     : {kb_rows}")
        print(f"  上传/导入入口    : {'有' if has_upload else '无'}")
        if kb_rows == 0:
            failures.append("知识库文档列表为空")
        if not has_upload:
            failures.append("缺少上传规范入口")
        page.screenshot(path=str(ARTIFACTS / "16-admin-knowledge.png"))
        print()

        browser.close()

    if page_errors:
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
    print("[OK] 管理员界面具备「用户管理」与「知识库管理」两项完整功能")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
