# -*- coding: utf-8 -*-
"""个人中心端到端验收（浏览器）：查看身份 + 自助改密 + 强制跳转拦截。

验证的核心风险
--------------
1. **强制跳转不能造成死锁**：被要求改密的用户必须能到个人中心，
   也必须还能**退出登录**（否则账号被锁死，只能改库）。
2. **改完必须能出去**：改密成功后守卫要放行，键是后端重新签发 token。
3. 身份信息必须可见（报告签认会用到，错了要能发现）。
4. 身份字段必须**只读**（审计依据不应由本人随意改）。

用法：python scripts/audit_profile_ui.py --base http://127.0.0.1:8080
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

OLD_PWD = "OldPass@123"
TEMP_PWD = "TempPass@111"
FINAL_PWD = "FinalPass@222"


def _in_container(code: str) -> subprocess.CompletedProcess:
    """在 api 容器内执行 python 片段（数据库只在容器网络内可达）。

    ⚠️ 必须显式指定 ``encoding="utf-8"``：Windows 的 subprocess 默认用
    系统 ANSI 代码页（GBK）解码，而容器输出永远是 UTF-8，
    遇到非 ASCII 字节会抛 ``UnicodeDecodeError`` 并让整个脚本崩掉
    （实际踩到：容器启动横幅里的中文直接崩了脚本）。
    """
    return subprocess.run(
        ["docker", "exec", "supervision-api", "python", "-c", code],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
    )


def _make_user(username: str, role: str = "engineer") -> None:
    """造一个可登录的验收账号。

    ⚠️ `session_scope()` 是**上下文管理器**，必须触发 `__exit__` 才会提交。
    之前写成 `s=session_scope(); db=s.__enter__()` 却从不退出 ——
    事务在连接归还时回滚，账号根本没建成功（表现为「登录时用户名或密码错误」）。
    这里显式调用 `__exit__` 提交。
    """
    code = (
        "import sys; sys.path.insert(0,'/app');"
        "from sqlalchemy import select;"
        "from app.core.security import hash_password;"
        "from app.db.models import Project, User;"
        "from app.db.session import session_scope;"
        "s=session_scope();db=s.__enter__();"
        "p=db.execute(select(Project).order_by(Project.id)).scalars().first();"
        "db.add(User(username=%(u)r, full_name='界面验收员', employee_no='UI%(n)s',"
        " org_name='某监理有限公司', department='项目监理部', position='专业监理工程师',"
        " cert_no='JL2026UI001', signature='界面署名',"
        " password_hash=hash_password(%(p)r), role=%(r)r,"
        " project_ids=[p.id] if p else [], specialties=['结构工程']));"
        "db.flush();s.__exit__(None,None,None);"
        "print('created')"
        % {"u": username, "n": uuid.uuid4().hex[:6].upper(), "p": OLD_PWD, "r": role}
    )
    result = _in_container(code)
    if "created" not in result.stdout:
        raise SystemExit(f"造数失败：{result.stdout} {result.stderr[-500:]}")


def _set_must_change(username: str, value: bool) -> None:
    code = (
        "import sys; sys.path.insert(0,'/app');"
        "from sqlalchemy import update;"
        "from app.db.models import User;"
        "from app.db.session import session_scope;"
        "s=session_scope();db=s.__enter__();"
        "db.execute(update(User).where(User.username==%(u)r).values(must_change_password=%(v)r));"
        "db.flush();s.__exit__(None,None,None);"
        "print('set')" % {"u": username, "v": value}
    )
    result = _in_container(code)
    if "set" not in result.stdout:
        raise SystemExit(f"设置改密标记失败：{result.stdout} {result.stderr[-500:]}")


def _cleanup(username: str) -> None:
    code = (
        "import sys; sys.path.insert(0,'/app');"
        "from sqlalchemy import select;"
        "from app.db.models import User;"
        "from app.db.session import session_scope;"
        "s=session_scope();db=s.__enter__();"
        "r=db.execute(select(User).where(User.username==%(u)r)).scalars().first();"
        "db.delete(r) if r else None;"
        "db.flush();s.__exit__(None,None,None);"
        "print('cleaned')" % {"u": username}
    )
    _in_container(code)


def _login(page, base: str, username: str, password: str) -> bool:
    """登录并等待应用外壳出现。失败时打印页面上的错误信息便于定位。"""
    page.goto(f"{base}/#/login", wait_until="networkidle", timeout=90000)
    page.evaluate("localStorage.clear()")
    page.goto(f"{base}/#/login", wait_until="networkidle", timeout=60000)
    page.wait_for_selector(".login-form", timeout=30000)
    inputs = page.locator(".login-form input")
    inputs.nth(0).fill(username)
    inputs.nth(1).fill(password)
    page.click('button:has-text("登 录")')
    page.wait_for_timeout(4500)

    if page.locator(".app-shell").count() > 0:
        return True

    # 登录失败：把页面上的错误提示打出来（否则只有一个 False，无从排查）
    try:
        alert = page.locator(".el-alert, .el-message").all_inner_texts()
        if alert:
            print(f"    [登录失败原因] {' | '.join(a.strip()[:160] for a in alert if a.strip())}")
        else:
            print(f"    [登录失败] 当前 URL={page.url} 无错误提示")
    except Exception as exc:  # noqa: BLE001
        print(f"    [登录失败] 读取提示异常：{exc}")
    return False


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    from playwright.sync_api import sync_playwright

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(ROOT / "var" / "pw-browsers"))

    username = f"ui_pf_{uuid.uuid4().hex[:6]}"
    failures: list[str] = []
    checks = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal checks
        checks += 1
        print(f"  [{'OK' if ok else 'X '}] {label}{('  ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    _make_user(username)

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, channel="msedge")
            context = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
            page = context.new_page()
            errors: list[str] = []
            page.on("pageerror", lambda exc: errors.append(str(exc)[:200]))

            # ---------- 1) 正常登录：个人中心可自助进入 ----------
            print("=== 1) 个人中心入口与身份展示 ===")
            check("登录成功", _login(page, base, username, OLD_PWD))
            nav = [t.strip() for t in page.locator(".app-nav").inner_text().split("\n") if t.strip()]
            print(f"  侧边栏: {nav}")
            check("侧边栏含「个人中心」", any("个人中心" in x for x in nav))

            page.goto(f"{base}/#/profile", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2500)
            body = page.inner_text("body")

            check("显示姓名", "界面验收员" in body)
            check("显示职务/岗位", "专业监理工程师" in body)
            check("显示所属部门", "项目监理部" in body)
            check("显示执业证号", "JL2026UI001" in body)
            check("显示签认署名", "界面署名" in body)
            check("说明身份由管理员维护", "由管理员维护" in body)
            check("含「修改密码」表单", "修改密码" in body and "当前密码" in body)
            check("无强制改密横幅（正常状态）", "请先设置新密码" not in body)
            page.screenshot(path=str(ARTIFACTS / "28-profile-normal.png"), full_page=True)

            # ---------- 2) 身份字段只读 ----------
            print()
            print("=== 2) 身份字段应只读 ===")
            # 个人中心不应出现「保存资料」这类会改身份的按钮
            editable_hints = ["保存资料", "保存身份", "编辑资料"]
            leaked = [h for h in editable_hints if h in body]
            check("无编辑身份按钮", not leaked, str(leaked) if leaked else "")

            # ---------- 3) 强制改密：拦截与死锁检查 ----------
            print()
            print("=== 3) 强制改密拦截 ===")
            _set_must_change(username, True)
            check("重新登录（已置改密标记）", _login(page, base, username, OLD_PWD))
            page.wait_for_timeout(1500)

            landed = (page.url.split("#")[-1] or "/").rstrip("/") or "/"
            print(f"  登录后落地: {landed}")
            check("被留在个人中心", landed == "/profile", landed)

            # 尝试去别的页面 → 应被弹回
            for target in ["reports", "chat", "knowledge"]:
                page.goto(f"{base}/#/{target}", wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(2000)
                cur = (page.url.split("#")[-1] or "/").rstrip("/") or "/"
                check(f"访问 /{target} 被弹回个人中心", cur == "/profile", cur)

            forced_body = page.inner_text("body")
            check("显示强制改密提示", "请先设置新密码" in forced_body)
            page.screenshot(path=str(ARTIFACTS / "28-profile-forced.png"), full_page=True)

            # ⚠️ 死锁检查：必须还能退出登录
            logout_btn = page.locator('button:has-text("退出")')
            check("强制改密时仍可退出登录（防死锁）", logout_btn.count() > 0)

            # ---------- 4) 自助改密并恢复访问 ----------
            print()
            print("=== 4) 自助改密并恢复访问 ===")
            page.goto(f"{base}/#/profile", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2000)
            pw_inputs = page.locator('.el-form input[type="password"]')
            check("改密表单有 3 个密码输入", pw_inputs.count() >= 3, f"{pw_inputs.count()} 个")
            pw_inputs.nth(0).fill(OLD_PWD)
            pw_inputs.nth(1).fill(FINAL_PWD)
            pw_inputs.nth(2).fill(FINAL_PWD)
            page.wait_for_timeout(600)

            # 强度实时提示应全部变绿（这里只验证页面不报错）
            page.locator('button:has-text("确认修改")').click()
            page.wait_for_timeout(4000)
            after = page.inner_text("body")
            check("提示密码已修改", "密码已修改" in after)
            page.screenshot(path=str(ARTIFACTS / "28-profile-changed.png"), full_page=True)

            # 改完后应能离开个人中心
            page.goto(f"{base}/#/reports", wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(2500)
            cur = (page.url.split("#")[-1] or "/").rstrip("/") or "/"
            print(f"  改密后访问 /reports 落地: {cur}")
            check("改密后可正常访问其他页面", cur == "/reports", cur)

            # ---------- 5) 新密码可登录 ----------
            print()
            print("=== 5) 新密码可登录 ===")
            check("用新密码登录成功", _login(page, base, username, FINAL_PWD))
            landed2 = (page.url.split("#")[-1] or "/").rstrip("/") or "/"
            check("登录后进本职落地页（非个人中心）", landed2 == "/evaluation", landed2)

            browser.close()

        if errors:
            for line in errors[:5]:
                print(f"  页面错误: {line}")
            failures.extend(errors[:5])
    finally:
        _cleanup(username)

    print()
    print("=" * 60)
    print(f"通过 {checks - len(failures)} / {checks} 项")
    if failures:
        for f in failures:
            print(f"  失败：{f}")
        return 1
    print("[OK] 个人中心：身份展示、自助改密、强制跳转拦截均符合预期，无死锁")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
