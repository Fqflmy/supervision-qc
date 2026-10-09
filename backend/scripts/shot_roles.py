# -*- coding: utf-8 -*-
"""逐角色截图：验证「不同角色看到不同界面」。

对每个演示身份登录后：
1. 记录侧边栏菜单项（断言与权限契约一致）；
2. 记录落地页；
3. 截图保存到 var/artifacts。

用法（需前后端已启动）：
    python scripts/shot_roles.py
    python scripts/shot_roles.py --base http://127.0.0.1:8080
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

#: 身份 -> (期望菜单数, 期望落地页)
#: 注：仅包含登录页提供的演示身份（后端 /auth/demo-identities）。
#: kb_manager 虽在权限表中有角色，但种子数据未创建该账号，因此无法在此验证。
#: 菜单数按「每个角色只显示与其职责相关的功能」原则确定。
EXPECTED = {
    "系统管理员": (8, "/users"),
    "监理工程师": (3, "/evaluation"),
    "审核人员": (2, "/judge"),
    "普通用户": (1, "/chat"),
}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "var" / "pw-browsers"))

    failures = 0
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel="msedge")
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)[:200]))

        print(f"  {'身份':14s} {'菜单数':>5s} {'落地页':14s} 菜单项")
        print("  " + "-" * 88)

        for label, (expect_count, expect_home) in EXPECTED.items():
            # 先导航到应用源，再清 localStorage ——
            # 首轮时页面还停在 about:blank，直接访问 localStorage 会被同源策略拒绝。
            page.goto(f"{base}/#/login", wait_until="networkidle", timeout=90000)
            page.evaluate("localStorage.clear()")
            page.goto(f"{base}/#/login", wait_until="networkidle", timeout=60000)
            page.wait_for_selector(".identity__card", timeout=30000)
            page.locator(".identity__card", has_text=label).first.click()
            page.wait_for_timeout(250)
            page.click('button:has-text("登 录")')
            page.wait_for_selector(".app-shell", timeout=30000)
            page.wait_for_timeout(1500)

            items = page.locator(".app-nav__label").all_inner_texts()
            items = [t.strip() for t in items if t.strip()]
            landed = (page.url.split("#")[-1] or "/").rstrip("/") or "/"

            ok_count = len(items) == expect_count
            ok_home = landed == expect_home
            if not (ok_count and ok_home):
                failures += 1
            flag = "OK" if (ok_count and ok_home) else "FAIL"
            print(f"  {label:14s} {len(items):5d} {landed:14s} {' / '.join(items)}  [{flag}]")

            safe = {
                "系统管理员": "admin",
                "监理工程师": "engineer",
                "审核人员": "expert",
                "普通用户": "viewer",
            }[label]
            page.screenshot(path=str(ARTIFACTS / f"15-role-{safe}.png"))

        browser.close()

    if errors:
        print()
        print("  页面错误：")
        for line in errors[:5]:
            print(f"    {line}")
        failures += len(errors[:5])

    print()
    print("[OK] 各角色菜单与落地页均符合预期" if not failures else f"[FAIL] {failures} 项不符")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
