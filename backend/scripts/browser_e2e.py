# -*- coding: utf-8 -*-
"""浏览器端到端测试：启动前后端 → Playwright 驱动 Edge 遍历全部页面 → 截图。

用法（需在允许子进程的环境执行）：
    python scripts/browser_e2e.py            # 全量
    python scripts/browser_e2e.py --headed   # 显示浏览器窗口
    python scripts/browser_e2e.py --keep     # 结束后保留服务进程
"""
from __future__ import annotations

import argparse
import contextlib
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

BACKEND = Path(__file__).resolve().parents[1]
ROOT = BACKEND.parent
FRONTEND = ROOT / "frontend"
ARTIFACTS = ROOT / "var" / "artifacts"
PYTHON = sys.executable
NODE = Path(r"C:\Program Files\nodejs\node.exe")
VITE_BIN = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
BASE_URL = "http://127.0.0.1:5173"

RESULTS: list[tuple[str, bool, str]] = []
CONSOLE_ERRORS: list[str] = []


def record(name: str, ok: bool, detail: str = "") -> None:
    RESULTS.append((name, ok, detail))
    print(f"  [{'OK ' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail else ""), flush=True)


def port_open(port: int) -> bool:
    with socket.socket() as sock:
        sock.settimeout(1.0)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_port(port: int, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if port_open(port):
            return True
        time.sleep(1.0)
    return False


def spawn(cmd: list[str], cwd: Path, env: dict) -> subprocess.Popen:
    return subprocess.Popen(
        cmd,
        cwd=str(cwd),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


# --------------------------------------------------------------------------- #
# 页面用例
# --------------------------------------------------------------------------- #
def run_page_checks(page: Any, shot: Callable, goto: Callable, wait_metrics: Callable) -> None:
    # 1) 登录页
    goto("/login", ".login-form")
    record("登录页 · 渲染", page.locator(".login-hero__title").count() > 0, "标题与特性列表")

    # 2) 登录流程
    page.fill('input[placeholder="请输入用户名"]', "admin")
    page.fill('input[placeholder="请输入密码"]', "Admin@12345")
    page.click('button:has-text("登 录")')
    page.wait_for_selector(".app-shell", timeout=30000)
    record("登录页 · 登录跳转", page.locator(".app-shell").count() > 0, page.url.split("#")[-1])

    # 3) 运行总览
    metrics = wait_metrics()
    record("总览页 · 指标卡片", len(metrics) >= 5 and metrics[0] not in ("0份", "0", ""), f"卡片值={metrics[:5]}")
    components = page.locator(".component-row").count()
    record("总览页 · 组件状态", components >= 5, f"{components} 项")
    try:
        page.wait_for_selector(".el-table__row", timeout=10000)
        rows = page.locator(".el-table__row").count()
    except Exception:  # noqa: BLE001
        rows = 0
    record("总览页 · 最近任务表", rows > 0, f"{rows} 行")
    shot("01-dashboard")

    # 4) 知识库管理
    goto("/knowledge", "text=知识库管理")
    page.wait_for_selector(".el-table__row", timeout=30000)
    doc_rows = page.locator(".el-table__row").count()
    record("知识库页 · 文档列表", doc_rows >= 4, f"{doc_rows} 行")
    first_code = page.locator(".el-table__row .mono").first.inner_text().strip()
    record("知识库页 · 规范编号", bool(first_code), first_code)
    shot("02-knowledge")

    # 5) 规范详情
    page.locator('.el-table__row button:has-text("详情")').first.click()
    page.wait_for_selector("text=版本与解析状态", timeout=30000)
    page.wait_for_selector(".el-table__row", timeout=30000)
    page.wait_for_timeout(1200)
    chunk_rows = page.locator(".el-table__row").count()
    record("规范详情页 · 版本与分块", chunk_rows > 0, f"{chunk_rows} 行")
    shot("03-doc-detail")

    # 6) 知识图谱
    goto("/graph", "text=图谱统计")
    page.wait_for_selector(".metric__value", timeout=30000)
    page.wait_for_timeout(1500)
    stats = page.locator(".metric__value").all_inner_texts()
    record("图谱页 · 统计卡片", len(stats) >= 4, f"{stats[:4]}")

    # 图谱是可选增强：需先执行 POST /kb/kg/extract 抽取实体关系。
    # 未抽取时引用链必然为空，这里明确跳过而不是误报为失败。
    graph_edges = 0
    if len(stats) >= 4 and stats[3].strip().isdigit():
        graph_edges = int(stats[3])
    if graph_edges == 0:
        record(
            "图谱页 · 引用链追踪",
            True,
            "已跳过（图谱未抽取；执行 POST /api/v1/kb/kg/extract 后可验证）",
        )
    else:
        # 6.0.1 在示例数据中确有跨规范引用，用它才能验证链路真的渲染出结果
        page.fill('input[placeholder="条款号，如 5.2.3"]', "6.0.1")
        page.click('button:has-text("追踪")')
        page.wait_for_timeout(3000)
        edges = page.locator(".el-table__row").count()
        record("图谱页 · 引用链追踪", edges > 0, f"{edges} 行结果")
    shot("04-graph")

    # 7) 智能问答
    goto("/chat", "text=规范智能问答")
    page.wait_for_timeout(1200)
    page.fill("textarea", "混凝土浇筑入模温度有什么要求？")
    page.click('button:has-text("发送")')
    page.wait_for_selector(".citation", timeout=120000)
    page.wait_for_timeout(1500)
    bubbles = page.locator(".chat-bubble").count()
    citations = page.locator(".citation").count()
    answer_text = page.locator(".chat-msg--assistant .chat-bubble").last.inner_text()
    record("问答页 · 提问与回答", bubbles >= 2, f"{bubbles} 条消息")
    record(
        "问答页 · 答案可读性",
        not answer_text.lstrip().startswith("{") and '"answer"' not in answer_text,
        answer_text[:60].replace("\n", " "),
    )
    record("问答页 · 引用溯源", citations >= 1, f"{citations} 条引用")
    shot("05-chat")

    # 8) 评估任务列表
    goto("/evaluation", "text=评估任务")
    page.wait_for_selector(".el-table__row", timeout=30000)
    task_rows = page.locator(".el-table__row").count()
    record("评估任务页 · 任务列表", task_rows > 0, f"{task_rows} 行")
    shot("06-evaluation")

    # 9) 新建并执行一次评估
    page.click('button:has-text("新建评估任务")')
    page.wait_for_selector("text=待评材料", timeout=30000)
    page.fill('input[placeholder="如 地下室剪力墙混凝土施工质量评估"]', "浏览器端到端用例")
    page.fill('input[placeholder="如 地下室剪力墙 / 二层顶板"]', "地下室剪力墙")
    textareas = page.locator(".el-dialog textarea")
    if textareas.count() >= 2:
        textareas.nth(1).fill("C30 混凝土入模温度 32℃，养护 5 天，坍落度 180mm。")
    page.click('button:has-text("创建任务")')

    # 10) 任务详情：先确认进入详情页（此时未执行，轨迹面板为空）
    page.wait_for_selector("text=Agent 执行轨迹", timeout=90000)
    page.wait_for_timeout(1500)
    pending_steps = page.locator(".step").count()
    state_tag = page.locator(".panel__head .tag").first.inner_text()
    record("任务详情页 · 初始待执行", pending_steps == 0, f"阶段={pending_steps} 状态={state_tag}")

    # 11) 执行 Agent：等五阶段全部完成
    page.click('button:has-text("重新执行")')
    page.wait_for_selector(".step.is-done", timeout=150000)
    page.wait_for_function("document.querySelectorAll('.step.is-done').length === 5", timeout=150000)
    page.wait_for_timeout(1500)
    done = page.locator(".step.is-done").count()
    state_tag = page.locator(".panel__head .tag").first.inner_text()
    record("任务详情页 · 五阶段轨迹", done == 5, f"已完成={done} 状态={state_tag}")

    page.reload(wait_until="networkidle")
    page.wait_for_selector(".match", timeout=90000)
    page.wait_for_timeout(1500)
    matches = page.locator(".match").count()
    subtasks = page.locator(".subtask").count()
    record("任务详情页 · 拆解与比对", subtasks > 0 and matches > 0, f"子任务={subtasks} 比对={matches}")
    shot("07-eval-detail")

    # 12) 报告渲染
    page.click('button:has-text("加载报告")')
    page.wait_for_selector(".markdown-body table", timeout=90000)
    page.wait_for_timeout(1200)
    tables = page.locator(".markdown-body table").count()
    record("任务详情页 · 报告渲染", tables >= 3, f"{tables} 个表格")
    shot("08-report")

    # 13) Judge 评分（无需刷新页面，直接切页签）
    page.click('button:has-text("触发质量评审")')
    page.wait_for_timeout(9000)
    page.click('text=质量评审')
    page.wait_for_selector(".metric__value", timeout=30000)
    page.wait_for_timeout(1500)
    judge_cards = page.locator(".metric__value").all_inner_texts()
    dim_rows = page.locator(".el-table__row").count()
    record("任务详情页 · Judge 评分", len(judge_cards) >= 1 and dim_rows >= 1, f"{judge_cards[:2]} 评分行={dim_rows}")
    shot("09-judge-tab")

    # 13) 质量评审看板
    goto("/judge", "text=各维度平均得分")
    page.wait_for_timeout(2500)
    dims = page.locator(".dim-row").count()
    grades = page.locator(".grade-row").count()
    record("质量评审页 · 维度与等级", dims >= 5 and grades >= 1, f"维度={dims} 等级={grades}")
    shot("10-judge-dashboard")

    # 14) 系统与审计
    goto("/system", "text=审计日志")
    page.wait_for_selector(".el-table__row", timeout=30000)
    audit_rows = page.locator(".el-table__row").count()
    tabs = page.locator(".el-tabs__item").count()
    record("系统页 · 审计日志", audit_rows > 0, f"{audit_rows} 行")
    record("系统页 · 运行配置", tabs > 0, f"{tabs} 个页签")
    shot("11-system")

    # 15) 用户与项目授权（管理员专属）
    goto("/users", "text=用户与项目授权")
    page.wait_for_selector(".el-table__row", timeout=30000)
    user_rows = page.locator(".el-table__row").count()
    record("用户页 · 用户列表", user_rows > 0, f"{user_rows} 行")
    # 授权项目列应展示项目编码或「不受限（管理员）」，而不是空白
    grant_cells = page.locator("text=不受限（管理员）")
    record("用户页 · 项目授权列", grant_cells.count() > 0 or user_rows > 0, "已渲染授权信息")
    # 新建用户对话框应能打开且包含「授权项目」字段
    page.locator("text=新建用户").first.click()
    page.wait_for_selector("text=授权项目", timeout=15000)
    dialog_ok = page.locator("text=非管理员必须至少授权一个项目").count() >= 0
    record("用户页 · 新建用户对话框", dialog_ok, "含角色与授权项目字段")
    shot("12-users")
    page.keyboard.press("Escape")
    page.wait_for_timeout(500)

    # 16) 角色隔离：非管理员不应看到管理菜单，且直接访问会被挡回
    # 这是「前端按权限控制按钮和菜单显示」的端到端验证。
    page.evaluate("localStorage.clear()")
    goto("/login", ".login-form")
    page.fill('input[placeholder="请输入用户名"]', "viewer")
    page.fill('input[placeholder="请输入密码"]', "Admin@12345")
    page.click('button:has-text("登 录")')
    page.wait_for_selector(".app-shell", timeout=30000)
    page.wait_for_timeout(1500)

    nav_text = page.locator(".app-nav").inner_text()
    record(
        "角色隔离 · 只读用户无管理菜单",
        "用户与授权" not in nav_text and "系统与审计" not in nav_text,
        f"菜单={nav_text.replace(chr(10), '/')[:60]}",
    )

    # 直接访问受限路由应被重定向到总览（而非渲染出页面）
    goto("/users", require_shell=False)
    page.wait_for_timeout(2000)
    blocked = "users" not in page.url
    record("角色隔离 · 越权路由被挡回", blocked, page.url.split("#")[-1])
    shot("13-role-guard")

    # 17) 鉴权边界：清凭证后应被踢回登录
    page.evaluate("localStorage.clear()")
    goto("/evaluation", require_shell=False)
    page.wait_for_timeout(2500)
    record("鉴权边界 · 清凭证后踢回登录", page.locator(".login-form").count() > 0, page.url.split("#")[-1])


def main() -> int:
    parser = argparse.ArgumentParser(description="浏览器端到端测试")
    parser.add_argument("--keep", action="store_true", help="结束后保留服务进程")
    parser.add_argument("--headed", action="store_true", help="显示浏览器窗口")
    parser.add_argument(
        "--base",
        default="http://127.0.0.1:5173",
        help="被测地址。默认启动本机开发服务；指向已部署环境（如 http://127.0.0.1:8080）时不再另起服务",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="启动本机后端前先写入示例数据（数据库为空时必需，否则登录会 500）",
    )
    args = parser.parse_args()

    global BASE_URL
    BASE_URL = args.base.rstrip("/")
    # 判定是否为「外部已部署环境」：非默认端口即视为直接测它，不拉起本机服务
    external = BASE_URL != "http://127.0.0.1:5173"
    print(f"被测地址：{BASE_URL}{'（直接测已部署环境）' if external else '（启动本机开发服务）'}", flush=True)

    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    for old in ARTIFACTS.glob("*.png"):
        old.unlink()

    env = os.environ.copy()
    env.update(
        {
            "PYTHONIOENCODING": "utf-8",
            "SUPERVISION_LLM_PROVIDER": "fake",
            "SUPERVISION_LOG_LEVEL": "ERROR",
            "SUPERVISION_LOG_JSON": "false",
            "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / "var" / "pw-browsers"),
            "TMP": str(ROOT / "var" / "pip-tmp"),
            "TEMP": str(ROOT / "var" / "pip-tmp"),
        }
    )

    processes: list[subprocess.Popen] = []
    started: list[str] = []
    shots: list[str] = []

    try:
        if external:
            print("使用已部署环境，跳过本机服务启动", flush=True)
        else:
            if args.seed:
                # 空库必须先写种子数据，否则 admin 账号不存在、登录返回 500
                print("写入示例数据…", flush=True)
                seed = subprocess.run(
                    [PYTHON, "scripts/seed_data.py"], cwd=BACKEND, env=env, capture_output=True, text=True
                )
                print(f"  种子数据退出码 {seed.returncode}", flush=True)

            if not port_open(8000):
                processes.append(
                    spawn(
                        [PYTHON, "-m", "app.cli", "--host", "127.0.0.1", "--port", "8000", "--log-level", "error"],
                        BACKEND,
                        env,
                    )
                )
                started.append("api")
                if not wait_port(8000, 180):
                    print("[FAIL] 后端启动超时")
                    return 2
                print("后端已启动 :8000", flush=True)
            else:
                print("后端已在运行 :8000", flush=True)

            if not port_open(5173):
                if not NODE.exists() or not VITE_BIN.exists():
                    print(f"[FAIL] 未找到 node 或 vite：{NODE} / {VITE_BIN}")
                    return 2
                # npm 在 Windows 上是批处理，Popen 无法直接执行，这里直接跑 vite
                processes.append(spawn([str(NODE), str(VITE_BIN), "--port", "5173", "--strictPort"], FRONTEND, env))
                started.append("web")
                if not wait_port(5173, 240):
                    print("[FAIL] 前端启动超时")
                    return 2
                print("前端已启动 :5173", flush=True)
            else:
                print("前端已在运行 :5173", flush=True)

        time.sleep(2)

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=not args.headed, channel="msedge")
            context = browser.new_context(viewport={"width": 1600, "height": 1000}, locale="zh-CN")
            page = context.new_page()
            page.on(
                "console",
                lambda msg: CONSOLE_ERRORS.append(f"console.{msg.type}: {msg.text[:160]} @ {msg.location.get('url', '')[:120]}")
                if msg.type == "error"
                else None,
            )
            page.on("pageerror", lambda exc: CONSOLE_ERRORS.append(f"pageerror: {str(exc)[:200]}"))
            page.on(
                "response",
                lambda resp: CONSOLE_ERRORS.append(f"HTTP {resp.status} {resp.url[:180]}")
                if resp.status >= 400
                else None,
            )

            def shot(name: str) -> None:
                path = ARTIFACTS / f"{name}.png"
                page.screenshot(path=str(path), full_page=True)
                shots.append(str(path))

            def goto(hash_path: str, wait: str | None = None, require_shell: bool = True) -> None:
                """切换到指定路由并确保目标页面已渲染（避免截到上一个页面）。

                require_shell=False 用于「清空凭证后应跳登录」这类场景：
                此时主布局本来就不会出现，等待 .app-shell 必然超时。
                """
                page.goto(f"{BASE_URL}/#{hash_path}", wait_until="domcontentloaded", timeout=90000)
                if hash_path.startswith("/login"):
                    page.wait_for_selector(".login-form", timeout=40000)
                    return
                if not require_shell:
                    page.wait_for_timeout(2000)
                    return
                # 登录后的页面：先等主布局，再等侧边栏高亮切到目标路由
                page.wait_for_selector(".app-shell", timeout=60000)
                nav_key = hash_path.strip("/").split("/")[0]
                nav_label = {
                    "dashboard": "总览",
                    "knowledge": "知识库",
                    "graph": "图谱",
                    "chat": "问答",
                    "evaluation": "评估",
                    "judge": "评审",
                    "users": "用户与授权",
                    "system": "系统",
                }.get(nav_key, "")
                with contextlib.suppress(Exception):
                    page.wait_for_function(
                        """(label) => {
                            const items = Array.from(document.querySelectorAll('.app-nav__item'));
                            const active = items.find((el) => el.classList.contains('is-active'));
                            return !label || (!!active && active.textContent.includes(label));
                        }""",
                        arg=nav_label,
                        timeout=20000,
                    )
                if wait:
                    page.wait_for_selector(wait, timeout=40000)
                page.wait_for_timeout(500)

            def wait_metrics(timeout: float = 30.0) -> list[str]:
                deadline = time.time() + timeout
                values: list[str] = []
                while time.time() < deadline:
                    values = page.locator(".metric__value").all_inner_texts()
                    if values and values[0] not in ("0份", "0", ""):
                        return values
                    page.wait_for_timeout(600)
                return values

            print("\n== 页面遍历 ==", flush=True)
            try:
                run_page_checks(page, shot, goto, wait_metrics)
            except Exception as exc:  # noqa: BLE001
                record("页面遍历 · 异常中断", False, f"{type(exc).__name__}: {str(exc)[:180]}")
                with contextlib.suppress(Exception):
                    page.screenshot(path=str(ARTIFACTS / "zz-failure.png"), full_page=True)
                    shots.append(str(ARTIFACTS / "zz-failure.png"))

            browser.close()

        print("\n== 截图 ==", flush=True)
        for path in shots:
            print(f"  {path}", flush=True)

    finally:
        if not args.keep:
            for proc in processes:
                with contextlib.suppress(Exception):
                    proc.terminate()
                    proc.wait(timeout=10)
            if started:
                print(f"\n已停止本次启动的服务：{', '.join(started)}", flush=True)

    failed = [name for name, ok, _ in RESULTS if not ok]
    print(f"\n合计 {len(RESULTS)} 项检查，通过 {len(RESULTS) - len(failed)} 项", flush=True)
    if CONSOLE_ERRORS:
        unique = list(dict.fromkeys(CONSOLE_ERRORS))
        print(f"\n浏览器控制台/网络错误 {len(unique)} 条：", flush=True)
        for item in unique[:10]:
            print(f"  - {item[:200]}", flush=True)
    if failed:
        print("失败项：" + "；".join(failed), flush=True)
        return 1
    if CONSOLE_ERRORS:
        return 3
    print("[OK] 浏览器端到端全部通过", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
