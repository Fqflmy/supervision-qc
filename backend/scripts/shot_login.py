# -*- coding: utf-8 -*-
"""截取登录页（含身份选择）与各角色落地页，用于人工确认视觉效果。

会依次以四个演示身份登录，记录实际落地路由并截图 ——
同时作为「按角色分流」的视觉证据。

用法：
    python scripts/shot_login.py            # 需前后端已启动（默认 5173）
    python scripts/shot_login.py --base http://127.0.0.1:8080
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

IDENTITIES = [
    ("监理工程师", "engineer", "evaluation"),
    ("审核人员", "expert", "judge"),
    ("普通用户", "viewer", "dashboard"),
    ("系统管理员", "admin", "users"),
]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:5173")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "var" / "pw-browsers"))

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel="msedge")
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)[:200]))

        page.goto(f"{base}/#/login", wait_until="networkidle", timeout=90000)
        page.wait_for_selector(".login-form", timeout=60000)
        page.wait_for_timeout(1200)
        page.screenshot(path=str(ARTIFACTS / "00-login.png"))
        cards = page.locator(".identity__card").count()
        print(f"  登录页已截图（身份卡片 {cards} 个）")

        print()
        print(f"  {'身份':12s} {'期望落地':12s} {'实际落地':14s} 结果")
        print("  " + "-" * 56)
        failures = 0
        for label, username, expect in IDENTITIES:
            page.evaluate("localStorage.clear()")
            page.goto(f"{base}/#/login", wait_until="networkidle", timeout=60000)
            page.wait_for_selector(".identity__card", timeout=30000)
            page.locator(".identity__card", has_text=label).first.click()
            page.wait_for_timeout(200)
            page.click('button:has-text("登 录")')
            page.wait_for_selector(".app-shell", timeout=30000)
            page.wait_for_timeout(1500)
            landed = page.url.split("#")[-1] or "/"
            ok = landed.rstrip("/") == f"/{expect}"
            if not ok:
                failures += 1
            print(f"  {label:12s} /{expect:11s} {landed:14s} {'OK' if ok else 'FAIL'}")
            page.screenshot(path=str(ARTIFACTS / f"14-home-{username}.png"))

        browser.close()

    if errors:
        print()
        print("  页面错误：")
        for line in errors[:5]:
            print(f"    {line}")
        failures += len(errors[:5])

    print()
    print("[OK] 截图完成，四类身份落地页均正确" if not failures else f"[FAIL] {failures} 项异常")
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
