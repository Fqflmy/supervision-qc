# -*- coding: utf-8 -*-
"""验证前端 Markdown 渲染的 XSS 净化（存储型 XSS 修复的回归测试）。

背景
----
`MarkdownView.vue` 用 `v-html` 渲染报告 Markdown，报告内容由大模型基于
**用户上传的规范文档**生成。若不做净化，攻击者可构造含脚本的 DOCX ->
入库 -> 生成报告 -> 管理员打开报告页即执行，形成存储型 XSS。

本脚本在**真实浏览器**中验证净化效果。相比 jsdom 单测，真实浏览器会实际执行
脚本，能观察到「载荷是否真的触发」，因此更接近真实攻击面。
（本项目受限环境无法安装 vitest/jsdom：npm 的落盘步骤会被静默丢弃。）

验证内容
--------
1. 14 种 XSS 载荷经 marked 渲染 + 净化后，不得残留可执行内容；
2. `window.__xssFired` 不得被置为 true（即脚本确实没执行）；
3. 正常 Markdown（标题/表格/代码块/列表/引用/外链）必须仍然正常渲染，
   防止「净化过度」把报告排版破坏掉。

用法：
    python backend/scripts/verify_xss_sanitize.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
FRONTEND = ROOT / "frontend"
NODE = Path(r"C:\Program Files\nodejs\node.exe")
VITE_BIN = FRONTEND / "node_modules" / "vite" / "bin" / "vite.js"
PORT = 5199


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


def main() -> int:
    if not NODE.exists() or not VITE_BIN.exists():
        print(f"[FAIL] 未找到 node 或 vite：{NODE} / {VITE_BIN}")
        return 2

    env = os.environ.copy()
    env.update(
        {
            "TMP": str(ROOT / "var" / "pip-tmp"),
            "TEMP": str(ROOT / "var" / "pip-tmp"),
            "PLAYWRIGHT_BROWSERS_PATH": str(ROOT / "var" / "pw-browsers"),
        }
    )

    print(f"启动 vite dev server :{PORT} …")
    proc = subprocess.Popen(
        [str(NODE), str(VITE_BIN), "--port", str(PORT), "--strictPort"],
        cwd=str(FRONTEND),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        if not wait_port(PORT, 180):
            print("[FAIL] vite 启动超时")
            return 2
        print("  已就绪")

        from playwright.sync_api import sync_playwright

        failures: list[str] = []
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True, channel="msedge")
            page = browser.new_page()
            console_errors: list[str] = []
            page.on("pageerror", lambda exc: console_errors.append(str(exc)[:200]))
            page.goto(
                f"http://127.0.0.1:{PORT}/xss-check.html",
                wait_until="networkidle",
                timeout=90_000,
            )
            page.wait_for_function("() => window.__xssProbe !== undefined", timeout=60_000)
            probe = page.evaluate("() => window.__xssProbe")
            normal = page.evaluate("() => document.getElementById('normal-host').innerText")
            browser.close()

        results = probe["results"]
        print()
        print(f"=== XSS 载荷验证（{len(results)} 项）===")
        for item in results:
            status = "FAIL" if item["leaked"] else "OK  "
            detail = f"  <- {item['reason']}" if item["leaked"] else ""
            print(f"  [{status}] {item['name']}{detail}")
            if item["leaked"]:
                failures.append(f"{item['name']}: {item['reason']}")

        print()
        print(f"=== 脚本是否被执行 ===  __xssFired = {probe['xssFired']}")
        if probe["xssFired"]:
            failures.append("有载荷在浏览器中实际执行（__xssFired 被置位）")

        print()
        print("=== 正常 Markdown 渲染（防止净化过度）===")
        for key, ok in probe["normalChecks"].items():
            print(f"  [{'OK  ' if ok else 'FAIL'}] {key}")
            if not ok:
                failures.append(f"正常 Markdown 渲染缺失: {key}")

        if console_errors:
            print()
            print("=== 页面错误 ===")
            for line in console_errors[:5]:
                print(f"  {line}")
            failures.extend(console_errors[:5])

        print()
        if failures:
            print(f"[FAIL] {len(failures)} 项未通过：")
            for line in failures:
                print(f"  {line}")
            return 1
        print("[OK] 全部 XSS 载荷被中和，且正常 Markdown 渲染不受影响")
        return 0
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:  # noqa: BLE001
            proc.kill()


if __name__ == "__main__":
    raise SystemExit(main())
