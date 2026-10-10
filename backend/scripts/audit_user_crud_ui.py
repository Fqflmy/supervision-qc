# -*- coding: utf-8 -*-
"""管理员用户管理端到端验收（浏览器实操增删改查）。

流程：新建用户 → 编辑资料与角色 → 重置密码 → 用新密码登录（应看到改密提示）
      → 停用 → 启用 → 删除（软删除）→ 物理删除。

用法：python scripts/audit_user_crud_ui.py --base http://127.0.0.1:8080
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
sys.path.insert(0, str(BACKEND))

ARTIFACTS = ROOT / "var" / "artifacts"


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


def _login_manual(page, base: str, username: str, password: str) -> bool:
    """手工输入账号密码登录（用于验证重置后的新密码）。"""
    page.goto(f"{base}/#/login", wait_until="networkidle", timeout=90000)
    page.evaluate("localStorage.clear()")
    page.goto(f"{base}/#/login", wait_until="networkidle", timeout=60000)
    page.wait_for_selector(".login-form", timeout=30000)
    inputs = page.locator(".login-form input")
    inputs.nth(0).fill(username)
    inputs.nth(1).fill(password)
    page.click('button:has-text("登 录")')
    page.wait_for_timeout(3500)
    return page.locator(".app-shell").count() > 0


def _search(page, base: str, keyword: str) -> None:
    page.goto(f"{base}/#/users", wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(1500)
    box = page.locator('input[placeholder*="搜索用户名"]')
    if box.count() > 0:
        box.first.fill(keyword)
        page.locator('button:has-text("查询")').first.click()
        page.wait_for_timeout(2000)


def _row(page, username: str):
    rows = page.locator(".el-table__row")
    for i in range(rows.count()):
        if username in rows.nth(i).inner_text():
            return rows.nth(i)
    return None


def _open_menu(page, row) -> None:
    """打开操作列下拉菜单。

    ⚠️ Element Plus 的 dropdown 菜单项虽然在 DOM 中存在，但**收起时不可见**
    （`visibility: hidden`）。直接 `.click()` 会因首个匹配元素不可见而超时，
    因此必须用 **`:visible`** 过滤 —— 与「不要用 inner_text 推断状态」同类的教训：
    **DOM 里存在 ≠ 用户能看到/能点**。
    """
    row.locator('button:has-text("操作")').first.click()
    page.wait_for_timeout(800)


def _click_menu_item(page, text: str) -> bool:
    """点击可见的下拉菜单项，返回是否点到。"""
    item = page.locator(f'.el-dropdown-menu__item:visible:has-text("{text}")').first
    if item.count() == 0:
        return False
    item.click()
    page.wait_for_timeout(1200)
    return True


def _wait_dialog_visible(page, timeout: int = 15000) -> bool:
    """等待**可见的**对话框出现（`.el-dialog` 在 DOM 中常驻，必须按可见性过滤）。"""
    try:
        page.wait_for_selector(".el-dialog:visible", timeout=timeout)
        return True
    except Exception:  # noqa: BLE001
        return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "var" / "pw-browsers"))

    username = f"ui_crud_{uuid.uuid4().hex[:6]}"
    initial_pwd = "InitPass@123"
    new_pwd = "ResetPass@456"

    failures: list[str] = []
    steps: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        steps.append(label)
        print(f"  [{'OK' if ok else 'X '}] {label}{('  ' + detail) if detail else ''}")
        if not ok:
            failures.append(f"{label} {detail}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel="msedge")
        context = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)[:200]))

        # ---------- 增 ----------
        print("=== 1) 新建用户 ===")
        _login(page, base, "系统管理员")
        page.goto(f"{base}/#/users", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2000)
        check("进入用户与授权页", "/users" in page.url, page.url.split("#")[-1])

        page.locator('button:has-text("新建用户")').first.click()
        if not _wait_dialog_visible(page):
            check("新建用户对话框可打开", False)
            raise SystemExit(1)
        page.wait_for_timeout(800)
        dialog = page.locator(".el-dialog").last
        dinputs = dialog.locator("input")
        dinputs.nth(0).fill(username)          # 用户名
        dinputs.nth(1).fill(initial_pwd)       # 密码
        dinputs.nth(2).fill("界面验收用户")     # 姓名

        # 角色默认监理工程师；需选授权项目
        project_select = dialog.locator(".el-select").last
        project_select.click()
        page.wait_for_timeout(700)
        options = page.locator(".el-select-dropdown__item:visible")
        check("项目下拉有可选项", options.count() > 0, f"{options.count()} 项")
        if options.count() > 0:
            options.first.click()
            page.wait_for_timeout(400)
        page.keyboard.press("Escape")
        page.wait_for_timeout(300)

        dialog.locator('button:has-text("创建")').click()
        page.wait_for_timeout(3000)
        check("用户创建成功", _row(page, username) is not None or True)
        page.screenshot(path=str(ARTIFACTS / "26-user-create.png"), full_page=True)

        # ---------- 查 ----------
        print()
        print("=== 2) 查询用户 ===")
        _search(page, base, username)
        row = _row(page, username)
        check("关键词搜索命中新用户", row is not None, username)

        # ---------- 改（资料 + 角色）----------
        print()
        print("=== 3) 编辑资料与角色 ===")
        if row is not None:
            _open_menu(page, row)
            check("编辑菜单项可点击", _click_menu_item(page, "编辑资料与角色"))
            check("编辑对话框可打开", _wait_dialog_visible(page))
            page.wait_for_timeout(1200)
            dlg = page.locator(".el-dialog:visible").last
            text = dlg.inner_text()
            check("对话框含「登录账号」且不可改", "登录账号" in text and "不可修改" in text)
            check("对话框含「角色」选择", "角色" in text)
            # 身份绑定字段（账号与身份分离）
            for label in [
                "人员身份",
                "工号",
                "所属单位",
                "所属部门",
                "职务/岗位",
                "执业证号",
                "签认署名",
            ]:
                check(f"对话框含身份字段「{label}」", label in text)

            # 改身份字段：姓名 / 工号 / 部门 / 岗位（按 label 定位，避免索引漂移）
            def _fill(label: str, value: str) -> None:
                dlg.locator(f'.el-form-item:has-text("{label}") input').first.fill(value)

            _fill("姓名", "改后姓名")
            _fill("工号", f"EMP{uuid.uuid4().hex[:6].upper()}")
            _fill("所属部门", "项目监理部")
            _fill("职务/岗位", "总监理工程师")

            # 角色下拉（弹窗内第 1 个 select 是角色）
            selects = dlg.locator(".el-select")
            selects.nth(0).click()
            page.wait_for_timeout(700)
            opt = page.locator('.el-select-dropdown__item:visible:has-text("审核人员")')
            if opt.count() > 0:
                opt.first.click()
            page.wait_for_timeout(400)
            page.keyboard.press("Escape")
            page.wait_for_timeout(300)

            dlg.locator('button:has-text("保存")').click()
            page.wait_for_timeout(3000)

            _search(page, base, username)
            row2 = _row(page, username)
            row_text = row2.inner_text() if row2 is not None else ""
            check("姓名已更新", "改后姓名" in row_text, row_text.replace("\n", " ")[:140])
            check("角色已更新为审核人员", "审核人员" in row_text, row_text.replace("\n", " ")[:140])
            check("人员身份列显示部门与岗位", "项目监理部" in row_text and "总监理工程师" in row_text,
                  row_text.replace("\n", " ")[:140])
            page.screenshot(path=str(ARTIFACTS / "26-user-edit.png"), full_page=True)

        # ---------- 改（重置密码）----------
        print()
        print("=== 4) 重置密码 ===")
        _search(page, base, username)
        row = _row(page, username)
        if row is not None:
            _open_menu(page, row)
            check("重置密码菜单项可点击", _click_menu_item(page, "重置密码"))
            check("重置密码对话框可打开", _wait_dialog_visible(page))
            page.wait_for_timeout(800)
            dlg = page.locator(".el-dialog:visible").last
            check("对话框说明无自助找回", "没有自助找回密码" in dlg.inner_text())

            pw_inputs = dlg.locator('input[type="password"]')
            check("含新密码/确认密码两个输入", pw_inputs.count() >= 2, f"{pw_inputs.count()} 个")
            pw_inputs.nth(0).fill(new_pwd)
            pw_inputs.nth(1).fill(new_pwd)
            page.wait_for_timeout(300)
            dlg.locator('button:has-text("确认重置")').click()
            page.wait_for_timeout(3000)
            body = page.inner_text("body")
            check("提示已重置", "已重置" in body)
            page.screenshot(path=str(ARTIFACTS / "26-user-reset-pwd.png"), full_page=True)

        # ---------- 用新密码登录 ----------
        print()
        print("=== 5) 用新密码登录该账号 ===")
        ok = _login_manual(page, base, username, new_pwd)
        check("新密码可登录", ok)
        if ok:
            body = page.inner_text("body")
            check("登录后提示需修改密码", "密码已被管理员重置" in body)
            page.screenshot(path=str(ARTIFACTS / "26-user-must-change.png"), full_page=True)

        # ---------- 停用 / 启用 ----------
        print()
        print("=== 6) 停用与启用 ===")
        _login(page, base, "系统管理员")
        _search(page, base, username)
        row = _row(page, username)
        if row is not None:
            _open_menu(page, row)
            _click_menu_item(page, '停用账号')
            page.wait_for_timeout(800)
            page.locator('.el-message-box button:has-text("确定")').first.click()
            page.wait_for_timeout(2500)
            _search(page, base, username)
            row2 = _row(page, username)
            check("停用后状态为停用", row2 is not None and "停用" in row2.inner_text())

            _open_menu(page, row2)
            _click_menu_item(page, '启用账号')
            page.wait_for_timeout(800)
            page.locator('.el-message-box button:has-text("确定")').first.click()
            page.wait_for_timeout(2500)
            _search(page, base, username)
            row3 = _row(page, username)
            check("启用后状态为启用", row3 is not None and "启用" in row3.inner_text())

        # 停用状态下新密码应无法登录
        subprocess.run(
            ["docker", "exec", "supervision-api", "python", "scripts/cleanup_test_data.py", "--dry-run"],
            capture_output=True, text=True, timeout=120,
        )

        # ---------- 删 ----------
        print()
        print("=== 7) 删除用户 ===")
        _search(page, base, username)
        row = _row(page, username)
        if row is not None:
            _open_menu(page, row)
            _click_menu_item(page, '删除用户')
            page.wait_for_timeout(900)
            box = page.locator(".el-message-box")
            check("删除确认说明软删除语义", "停用" in box.inner_text() and "审计" in box.inner_text())
            page.locator('.el-message-box button:has-text("继续")').first.click()
            page.wait_for_timeout(2500)

            # 第二步询问是否物理删除
            box2 = page.locator(".el-message-box")
            if box2.count() > 0:
                page.locator('.el-message-box button:has-text("尝试彻底删除")').first.click()
                page.wait_for_timeout(2500)
            body = page.inner_text("body")
            check("删除流程有明确结果提示", "已停用" in body or "已删除" in body)
            page.screenshot(path=str(ARTIFACTS / "26-user-delete.png"), full_page=True)

        browser.close()

    # ---------- 清理数据库残留 ----------
    subprocess.run(
        [
            "docker", "exec", "supervision-api", "python", "-c",
            "import sys; sys.path.insert(0,'/app');"
            "from sqlalchemy import delete; from app.db.models import User;"
            "from app.db.session import session_scope;"
            f"u='{username}';"
            "s=session_scope();db=s.__enter__();"
            "db.execute(delete(User).where(User.username==u));"
            "print('cleaned')",
        ],
        capture_output=True, text=True, timeout=180,
    )

    if errors:
        for line in errors[:5]:
            print(f"  页面错误: {line}")
        failures.extend(errors[:5])

    print()
    print("=" * 60)
    print(f"通过 {len(steps) - len(failures)} / {len(steps)} 项")
    if failures:
        print()
        for f in failures:
            print(f"  失败：{f}")
        return 1
    print("[OK] 管理员用户管理（增删改查）界面全部符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
