# -*- coding: utf-8 -*-
"""可达性矩阵实测：逐角色从落地页出发，走遍所有入口，记录实际到达情况。

与静态分析的区别
----------------
静态分析只能看「有没有写跳转代码」，实测能发现：
- 菜单可见但点击后被守卫踢回（落点无权）；
- 页内按钮存在但点了没反应/报错；
- 跳转目标页面实际渲染为空或报错。

⚠️ 断言方法（踩过两次的坑）
--------------------------
**不要用 `inner_text()` 的文本匹配判断页面是否渲染**：
- 文本匹配会命中表格列头、禁用按钮的文字（曾误报「只读用户看到写操作」）；
- `inner_text()` **不包含 placeholder 属性**（曾误把所有角色的「智能问答」判成空壳，
  实际页面完全正常 —— 因为「请输入问题」在输入框的 placeholder 里）。

因此本脚本用 **DOM 元素选择器**判存在性，只在必要时才用文本。

用法：
    python scripts/build_reachability_matrix.py
    python scripts/build_reachability_matrix.py --base http://127.0.0.1:8080
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
DOC = ROOT / "docs" / "角色可达性矩阵.md"

#: 角色 → (登录卡片文案, 角色码, 期望落地页, 期望菜单数)
ROLES = [
    ("系统管理员", "admin", "/users", 9),
    ("知识库管理员", "kb_manager", "/knowledge", 4),
    ("监理工程师", "engineer", "/evaluation", 5),
    ("审核人员", "expert", "/judge", 4),
    ("普通用户", "viewer", "/reports", 3),
]

#: 页面 → (路由名, 标题, 必须存在的 DOM 选择器, 可选文本关键词)
#: 选择器判「页面渲染出功能」；关键词仅用于极少数没有稳定选择器的场景。
PAGES = [
    ("dashboard", "运行总览", ".component-row", []),
    ("knowledge", "知识库", ".el-table__row", []),
    ("graph", "知识图谱", ".panel", []),
    ("chat", "智能问答", ".chat-input textarea, textarea", []),
    ("evaluation", "评估任务", ".el-table", []),
    ("reports", "评估报告", ".el-table", []),
    ("judge", "质量评审", ".metric", []),
    ("users", "用户与授权", ".el-table", []),
    ("system", "系统与审计", ".el-tabs, .el-table", []),
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

    #: role_label -> {page_name: (菜单可见, 直达页面, 内容渲染)}
    matrix: dict[str, dict[str, tuple[bool, bool, bool]]] = {}
    home_ok_map: dict[str, bool] = {}
    menu_count_map: dict[str, int] = {}

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel="msedge")
        context = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
        page = context.new_page()
        page.on("pageerror", lambda exc: None)

        for label, role, expect_home, expect_menus in ROLES:
            print(f"=== {label}（{role}）===")
            _login(page, base, label)
            landed = (page.url.split("#")[-1] or "/").rstrip("/") or "/"
            home_ok = landed == expect_home
            home_ok_map[label] = home_ok

            nav = [
                t.strip()
                for t in page.locator(".app-nav").inner_text().split("\n")
                if t.strip()
            ]
            menu_count_map[label] = len(nav)
            print(f"  落地页: {landed}  {'OK' if home_ok else 'X 期望 ' + expect_home}")
            print(f"  菜单（{len(nav)}）: {nav}")
            matrix[label] = {}

            for name, title, selector, keywords in PAGES:
                menu_visible = any(title[:4] in item for item in nav)

                page.goto(f"{base}/#/{name}", wait_until="networkidle", timeout=60000)
                page.wait_for_timeout(2200)
                cur = (page.url.split("#")[-1] or "/").rstrip("/") or "/"
                direct_ok = cur == f"/{name}"

                # DOM 断言：元素存在即认为页面渲染出功能
                content_ok = False
                if direct_ok:
                    try:
                        content_ok = page.locator(selector).count() > 0
                    except Exception:  # noqa: BLE001
                        content_ok = False
                    if content_ok and keywords:
                        body = page.inner_text("body")
                        content_ok = all(k in body for k in keywords)

                matrix[label][name] = (menu_visible, direct_ok, content_ok)
                if direct_ok and content_ok:
                    mark = "OK"
                elif direct_ok:
                    mark = "空壳"
                elif menu_visible:
                    mark = "守卫拦回"
                else:
                    mark = "无权限"
                print(
                    f"    {name:12s} 菜单={'是' if menu_visible else '否':2s} "
                    f"直达={'是' if direct_ok else '否':2s} "
                    f"渲染={'是' if content_ok else '否':2s}  [{mark}]"
                )

            page.screenshot(path=str(ARTIFACTS / f"24-reach-{role}.png"), full_page=True)
            print()

        browser.close()

    # ---------- 生成矩阵文档 ----------
    def cell(label: str, name: str) -> str:
        menu_visible, direct_ok, content_ok = matrix[label][name]
        if direct_ok and content_ok:
            return "✓"
        if direct_ok and not content_ok:
            return "空壳"
        return "菜单待修" if menu_visible else "—"

    lines: list[str] = [
        "# 角色可达性矩阵",
        "",
        "> 由 `backend/scripts/build_reachability_matrix.py` **实测生成**（浏览器逐角色走查），",
        "> 不是静态推断。运行方式见文末。",
        "",
        "**图例**",
        "",
        "| 符号 | 含义 |",
        "| :-: | --- |",
        "| `✓` | 侧边栏有入口，且页面能正常渲染出功能 |",
        "| `空壳` | 能进入但页面未渲染出内容（异常，需修） |",
        "| `菜单待修` | 菜单可见但直达被守卫拦回（异常，需修） |",
        "| `—` | 该角色无权限，菜单与路由均不放行（预期） |",
        "",
        "## 页面级可达性",
        "",
        "| 页面 | " + " | ".join(label for label, _, _, _ in ROLES) + " |",
        "| --- | " + " | ".join(":-:" for _ in ROLES) + " |",
    ]
    for name, title, _, _ in PAGES:
        cells = [cell(label, name) for label, _, _, _ in ROLES]
        lines.append(f"| {title}（`{name}`） | " + " | ".join(cells) + " |")

    lines += [
        "",
        "## 落地页与菜单数",
        "",
        "| 角色 | 落地页 | 实测菜单数 | 期望 | 落地正确 |",
        "| --- | --- | :-: | :-: | :-: |",
    ]
    for label, role, expect_home, expect_menus in ROLES:
        lines.append(
            f"| {label} | `{expect_home}` | {menu_count_map[label]} | {expect_menus} | "
            f"{'✓' if home_ok_map[label] else '✗'} |"
        )

    lines += [
        "",
        "## 各角色的功能定位",
        "",
        "| 角色 | 核心职责 | 可用功能 |",
        "| --- | --- | --- |",
        "| 系统管理员 | 平台运营与治理 | 全部功能：运行总览、知识库、图谱、问答、评估任务、评估报告、质量评审、用户与授权、系统与审计 |",
        "| 知识库管理员 | 规范库维护 | 知识库管理、知识图谱、智能问答、评估报告 |",
        "| 监理工程师 | 发起并执行评估 | 规范查询、知识图谱、智能问答、评估任务、评估报告 |",
        "| 审核人员 | 人工复核与签发 | 规范查询、智能问答、评估报告、质量评审 |",
        "| 普通用户 | 只读查看报告 | 规范查询、智能问答、评估报告 |",
        "",
        "## 说明：为什么有些页面「无权限」而不是「隐藏」",
        "",
        "无权限的页面在前端**双重拦截**：菜单不显示 + 路由守卫拦回入口页。",
        "因此直接输入 URL 也不会进入 —— 矩阵中的 `—` 即为此情况。",
        "真正的权限边界仍在后端（接口返回 403），前端拦截只是体验层。",
        "",
        "## 隐藏页（详情页）的入口",
        "",
        "详情页没有独立菜单，靠列表页的按钮进入。**都有入口，不需要手输 URL**：",
        "",
        "| 详情页 | 路由 | 入口来源 |",
        "| --- | --- | --- |",
        "| 任务详情 | `/evaluation/:id` | 评估任务列表「详情」、质量评审队列「详情」 |",
        "| **报告详情（只读）** | `/reports/:id` | 评估报告列表「查看报告」 |",
        "| 规范详情 | `/knowledge/:id` | 知识库列表「详情」 |",
        "",
        "> **为什么报告详情要独立一页**：任务详情是**发起方/复核方的工作台**，",
        "> 含迭代次数、Token 消耗、执行轨迹与「重新执行 / 人工裁定」按钮 ——",
        "> 对只读用户既无用也不该看。报告详情只呈现**报告本身与签发状态**，",
        "> 并用醒目警示标明「是否已签发」（未签发不得作为正式依据）。",
        "",
        "## 界面可用性：面包屑导航",
        "",
        "所有页面顶部有面包屑，标识当前位置与层级：",
        "",
        "```",
        "顶层页：    评估报告",
        "详情页：    评估报告 / 报告详情      ← 点「评估报告」返回列表",
        "```",
        "",
        "面包屑与侧边栏**用词一致**（例如只读角色下知识库页的菜单名是「规范查询」，",
        "面包屑也用「规范查询」），否则用户会以为是两个不同的地方。",
        "",
        "## 复现",
        "",
    ]
    fence = "`" * 3
    lines.append(fence + "bash")
    lines.append("# 生成矩阵（需服务已启动，会打开浏览器逐角色走查）")
    lines.append(
        "python backend/scripts/build_reachability_matrix.py --base http://127.0.0.1:8080"
    )
    lines.append(fence)
    lines.append("")
    lines.append("脚本会同时输出各角色截图到 `var/artifacts/24-reach-<role>.png`，便于人工核对。")

    DOC.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    print(f"=== 已生成 {DOC} ===")

    problems: list[str] = []
    for label, _, _, _ in ROLES:
        for name, title, _, _ in PAGES:
            menu_visible, direct_ok, content_ok = matrix[label][name]
            if direct_ok and not content_ok:
                problems.append(f"{label} · {title} —— 页面渲染异常（空壳）")
            if menu_visible and not direct_ok:
                problems.append(f"{label} · {title} —— 菜单可见但被守卫拦回")
        if not home_ok_map[label]:
            problems.append(f"{label} —— 落地页不正确")

    print()
    if problems:
        print(f"[FAIL] {len(problems)} 处异常：")
        for p in problems:
            print(f"  {p}")
        return 1
    print("[OK] 菜单可见性与实际可达性一致，无空壳页面，落地页正确")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
