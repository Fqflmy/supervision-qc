# -*- coding: utf-8 -*-
"""登录页企业化验收（浏览器截图 + 关键文案断言）。

关注点：
1. 文案是否**面向业务价值**，而不是罗列技术栈；
   （旧版有「Multi-Query + RRF + BGE 重排」这类只有开发者关心的词）
2. 是否具备企业系统的框架要素：机构标识区、安全责任提示、页脚；
3. 是否**没有虚假合规信息**（未配置备案号时不得出现占位符）。

用法：python scripts/audit_login_ui.py --base http://127.0.0.1:8080
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

#: 不应再出现的「技术演示」文案（对业务方没有决策价值）
TECH_JARGON = ["Multi-Query", "RRF", "BGE", "Neo4j", "LangGraph", "LLM-as-Judge", "embedding"]

#: 不得出现的虚假合规信息
FAKE_LEGAL = ["ICP备000", "示例公司", "xxx公司", "待填写", "TODO"]


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
        context = browser.new_context(viewport={"width": 1600, "height": 900}, locale="zh-CN")
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)[:200]))

        page.goto(f"{base}/#/login", wait_until="networkidle", timeout=90000)
        page.evaluate("localStorage.clear()")
        page.goto(f"{base}/#/login", wait_until="networkidle", timeout=60000)
        page.wait_for_selector(".login-page", timeout=30000)
        page.wait_for_timeout(2500)

        body = page.inner_text("body")

        print("=== 1) 不应再出现的「技术演示」文案 ===")
        leaked = [k for k in TECH_JARGON if k in body]
        print(f"  命中: {leaked if leaked else '无'}")
        if leaked:
            failures.append(f"登录页仍在罗列技术栈：{leaked}")

        print()
        print("=== 2) 企业系统框架要素 ===")
        checks = {
            "机构标识区": page.locator(".login-brand").count() > 0,
            "价值主张": page.locator(".value-item").count() >= 3,
            "部署形态标签": page.locator(".meta-chip").count() >= 1,
            "统一身份认证标题": "统一身份认证" in body,
            "安全责任提示": page.locator(".login-panel__notice").count() > 0,
            "页脚": page.locator(".login-panel__footer").count() > 0,
            "记住用户名": "记住用户名" in body,
            "登录帮助入口": "登录遇到问题" in body,
        }
        for label, ok in checks.items():
            print(f"  [{'OK' if ok else 'X '}] {label}")
            if not ok:
                failures.append(f"缺少「{label}」")

        print()
        print("=== 3) 不得出现虚假合规信息 ===")
        fake = [k for k in FAKE_LEGAL if k in body]
        print(f"  命中: {fake if fake else '无'}")
        if fake:
            failures.append(f"出现虚假/占位合规信息：{fake}")

        print()
        print("=== 4) 登录表单可用性 ===")
        print(f"  用户名输入框: {page.locator('.login-form input').count() > 0}")
        print(f"  登录按钮    : {page.locator('button:has-text(\"登 录\")').count() > 0}")

        page.screenshot(path=str(ARTIFACTS / "23-login-enterprise.png"), full_page=True)

        # 顺手验证「登录遇到问题」对话框可打开
        link = page.locator(".login-form__link")
        if link.count() > 0:
            link.first.click()
            page.wait_for_timeout(900)
            dialog_ok = page.locator(".help").count() > 0
            print(f"  帮助对话框  : {dialog_ok}")
            if not dialog_ok:
                failures.append("「登录遇到问题」对话框打不开")
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)

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
    print("[OK] 登录页已企业化：业务文案、企业框架要素齐备，无虚假合规信息")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
