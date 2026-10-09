# -*- coding: utf-8 -*-
"""验收本次修改涉及的两个关键界面：

1. 普通用户进入「规范查询」→ 应能浏览规范，且**写操作按钮处于禁用/非链接状态**；
2. 监理工程师打开「新建评估任务」→ 应有**项目选择且单项目自动选中**。

⚠️ 检查方法说明（踩过的坑）
---------------------------
第一版用文本匹配判断「是否出现写操作」（如 `"解析" in body`），结果**误报**：
- 「解析」「发布」既是按钮文字、也是表格列头文字；
- 按钮虽被禁用，文字仍在 DOM 中。

因此改为检查**状态**而非文字：
- 按钮：`is_disabled()`；
- 行内操作：是否为可点击的 `<a>` / `button`（禁用态渲染为纯文本 `<span>`）。
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


def _login(page, base: str, label: str) -> None:
    page.goto(f"{base}/#/login", wait_until="networkidle", timeout=90000)
    page.evaluate("localStorage.clear()")
    page.goto(f"{base}/#/login", wait_until="networkidle", timeout=60000)
    page.wait_for_selector(".identity__card", timeout=30000)
    page.locator(".identity__card", has_text=label).first.click()
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
        context = browser.new_context(viewport={"width": 1440, "height": 900}, locale="zh-CN")
        page = context.new_page()
        page_errors: list[str] = []
        page.on("pageerror", lambda exc: page_errors.append(str(exc)[:200]))

        # ---------- 1) 普通用户：只读规范查询 ----------
        print("=== 普通用户 · 规范查询（只读）===")
        _login(page, base, "普通用户")
        page.goto(f"{base}/#/knowledge", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)

        nav_items = [
            t.strip() for t in page.locator(".app-nav").inner_text().split("\n") if t.strip()
        ]
        rows = page.locator(".el-table__row").count()
        body = page.inner_text("body")
        print(f"  侧边栏菜单项    : {nav_items}")
        print(f"  文档表格行数    : {rows}")
        print(f"  标题含「规范查询」: {'规范查询' in body}")

        # —— 写操作按钮必须是禁用态 ——
        upload = page.locator('button:has-text("上传规范")')
        if upload.count() == 0:
            print("  上传按钮        : 不存在（只读下也可接受）")
            upload_disabled = True
        else:
            upload_disabled = upload.first.is_disabled()
            print(f"  上传按钮禁用    : {upload_disabled}")
        if not upload_disabled:
            failures.append("只读用户可点击「上传规范」")

        # —— 行内操作不应是可点击链接 ——
        first_row = page.locator(".el-table__row").first
        links = first_row.locator("a, button.el-button--primary.is-link, button.el-button--primary")
        clickable = [
            links.nth(i).inner_text().strip()
            for i in range(links.count())
            if links.nth(i).is_visible() and links.nth(i).is_enabled()
        ]
        print(f"  首行可点击操作  : {clickable}")
        # 「详情」是只读操作，允许可点；写操作（解析/下线/发布/图谱）不应可点
        write_actions = {"解析", "下线", "发布", "图谱", "编辑"}
        leaked = [c for c in clickable if c in write_actions]
        if leaked:
            failures.append(f"只读用户可点击写操作：{leaked}")

        if rows == 0:
            failures.append("普通用户看不到任何规范文档（只读浏览不可用）")
        if "规范查询" not in body:
            failures.append("普通用户未看到「规范查询」标题")
        page.screenshot(path=str(ARTIFACTS / "17-viewer-knowledge.png"))

        # ---------- 2) 工程师：新建任务有项目选择 ----------
        print()
        print("=== 监理工程师 · 新建评估任务 ===")
        _login(page, base, "监理工程师")
        page.goto(f"{base}/#/evaluation", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(1500)

        create_btn = page.locator('button:has-text("新建评估任务")')
        if create_btn.count() == 0:
            failures.append("工程师看不到「新建评估任务」按钮")
            print("  [X] 找不到新建按钮")
        else:
            print(f"  「新建评估任务」可点: {create_btn.first.is_enabled()}")
            if not create_btn.first.is_enabled():
                failures.append("工程师的「新建评估任务」按钮被禁用")

            create_btn.first.click()
            page.wait_for_selector(".el-dialog", timeout=15000)
            page.wait_for_timeout(800)

            dialog = page.locator(".el-dialog")
            has_project = "所属项目" in dialog.inner_text()
            print(f"  对话框含「所属项目」: {has_project}")
            if not has_project:
                failures.append("新建任务对话框缺少项目选择")

            # Element Plus 的单选下拉：已选项渲染在 .el-select__selected-item
            # （不能用 .el-select__placeholder —— 那是未选时的占位符）
            selected = dialog.locator(".el-select__selected-item")
            sel_text = " | ".join(
                selected.nth(i).inner_text().strip()
                for i in range(selected.count())
                if selected.nth(i).inner_text().strip()
            )
            print(f"  已选中的下拉项  : {sel_text[:90]}")
            if has_project and "DEMO" not in sel_text and "示范" not in sel_text:
                failures.append(f"单项目未自动选中（当前显示：{sel_text[:60]!r}）")

            page.screenshot(path=str(ARTIFACTS / "17-engineer-create-task.png"))
            page.keyboard.press("Escape")
            page.wait_for_timeout(400)

        # ---------- 3) 知识库管理员：管理态仍可写 ----------
        print()
        print("=== 知识库管理员 · 管理态（可写）===")
        _login(page, base, "知识库管理员")
        page.goto(f"{base}/#/knowledge", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)

        km_body = page.inner_text("body")
        km_upload = page.locator('button:has-text("上传规范")')
        km_enabled = km_upload.count() > 0 and km_upload.first.is_enabled()
        print(f"  标题含「知识库管理」: {'知识库管理' in km_body}")
        print(f"  上传按钮可用    : {km_enabled}")
        if not km_enabled:
            failures.append("知识库管理员的上传按钮不可用（管理态被误禁）")
        if "知识库管理" not in km_body:
            failures.append("知识库管理员未看到「知识库管理」标题（管理态标题错误）")
        page.screenshot(path=str(ARTIFACTS / "17-kbmanager-knowledge.png"))

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
    print("[OK] 只读规范查询、工程师新建任务、知识库管理员管理态均符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
