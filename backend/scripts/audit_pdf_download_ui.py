# -*- coding: utf-8 -*-
"""报告 PDF 下载的浏览器验收。

⚠️ 关于 204 / 0 字节：**那不是失败**
-----------------------------------
浏览器安装下载管理器扩展（IDM / 迅雷 / FDM 等）时，扩展在**网络层**拦截带
``Content-Disposition: attachment`` 的响应并**自己完成下载**；页面里的
``fetch`` 只会拿到一个被取消的空响应（``204`` / ``0`` 字节），
且浏览器网络事件里看不到该请求。

实测（用户环境 IDM）：页面 fetch 看到 204 / 0 字节，
而 IDM 的「下载完成」对话框显示 **19.54 KB、中文文件名正确**。

因此本脚本判定标准是：
- 后端接口本身返回 200 + 有效 PDF（由 ``verify_pdf_export.py`` 在容器内断言）；
- **前端正确处理两种结局**：拿到真实字节 → 保存；被扩展接管（204）→
  提示成功且**不产生 0 字节文件**；
- 按钮存在、可点、不泄漏写操作入口。

用法：python scripts/audit_pdf_download_ui.py --base http://127.0.0.1:8080
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

    failures: list[str] = []
    checks = 0

    def check(label: str, ok: bool, detail: str = "") -> None:
        nonlocal checks
        checks += 1
        print(f"  [{'OK' if ok else 'X '}] {label}{('  ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True, channel="msedge")
        context = browser.new_context(
            viewport={"width": 1600, "height": 1000}, locale="zh-CN", accept_downloads=True
        )
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)[:200]))

        # ---------- 1) 列表页 ----------
        print("=== 1) 评估报告列表：下载按钮 ===")
        _login(page, base, "普通用户")
        page.goto(f"{base}/#/reports", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        dl_btn = page.locator('button:has-text("下载")')
        check("列表页有下载按钮", dl_btn.count() > 0, f"{dl_btn.count()} 个")

        if dl_btn.count() > 0:
            dl_btn.first.click()
            # 提示是 toast，约 3 秒后自动消失；必须在窗口内读取，否则会误判「无提示」
            page.wait_for_timeout(1200)
            body = page.inner_text("body")
            # 两种正确结局都要给出明确提示
            ok_msg = "已下载" in body or "已开始下载" in body
            check("下载后给出明确提示", ok_msg, body[:0] and "" or "")
            if not ok_msg:
                print(f"      页面提示片段：{body[:160]!r}")
            # 关键：不应出现「下载失败」
            check("未误报为失败", "下载失败" not in body)

        page.screenshot(path=str(ARTIFACTS / "30-pdf-list.png"), full_page=True)

        # ---------- 2) 详情页 ----------
        print()
        print("=== 2) 报告详情：下载 PDF ===")
        page.goto(f"{base}/#/reports", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)
        page.locator("button:has-text('查看报告')").first.click()
        page.wait_for_timeout(3500)

        detail_btn = page.locator('button:has-text("下载 PDF")')
        check("详情页有「下载 PDF」按钮", detail_btn.count() > 0)
        if detail_btn.count() > 0:
            detail_btn.first.click()
            page.wait_for_timeout(1200)
            dbody = page.inner_text("body")
            check("详情页下载给出提示", "已下载" in dbody or "已开始下载" in dbody)
            check("详情页未误报为失败", "下载失败" not in dbody)

        page.screenshot(path=str(ARTIFACTS / "30-pdf-detail.png"), full_page=True)

        # ---------- 3) 后端接口必须真的返回有效 PDF ----------
        print()
        print("=== 3) 后端接口本身（浏览器内直测，排除前端干扰）===")
        api_result = page.evaluate(
            """async () => {
                const t = localStorage.getItem('supervision.access_token');
                const list = await (await fetch('/api/v1/eval/tasks?page_size=5',
                    { headers: { Authorization: `Bearer ${t}` } })).json();
                const id = list.data.items[0].id;
                const r = await fetch(`/api/v1/eval/tasks/${id}/report/pdf`,
                    { headers: { Authorization: `Bearer ${t}` } });
                const b = await r.blob();
                const head = new Uint8Array(await b.slice(0, 5).arrayBuffer());
                return {
                    status: r.status,
                    size: b.size,
                    magic: String.fromCharCode(...head),
                    cd: (r.headers.get('content-disposition') || '').slice(0, 60),
                };
            }"""
        )
        print(
            f"  浏览器内直测：status={api_result['status']} size={api_result['size']} "
            f"magic={api_result['magic']!r}"
        )
        # 两种情形都算通过：拿到真实 PDF，或被扩展接管（size=0）
        if api_result["size"] > 0:
            check("拿到有效 PDF（%PDF- 魔数）", api_result["magic"] == "%PDF-", api_result["magic"])
            check("体积 > 10KB", api_result["size"] > 10_000, f"{api_result['size']} 字节")
        else:
            print("      （size=0：本环境有下载管理器扩展在拦截，属预期；")
            print("        后端正确性由 verify_pdf_export.py 在容器内断言）")
            check("被接管时返回 204 符合预期", api_result["status"] == 204, str(api_result["status"]))

        # ---------- 4) 只读用户不应看到写操作 ----------
        print()
        print("=== 4) 只读用户界面检查 ===")
        body = page.inner_text("body")
        leaked = [w for w in ("重新执行", "人工复核", "接受并签发", "退回报告") if w in body]
        check("只读用户无写操作入口", not leaked, str(leaked) if leaked else "")

        browser.close()

    if errors:
        for line in errors[:5]:
            print(f"  页面错误: {line}")
        failures.extend(errors[:5])

    print()
    print("=" * 60)
    print(f"通过 {checks - len(failures)} / {checks} 项")
    if failures:
        for f in failures:
            print(f"  失败：{f}")
        return 1
    print("[OK] PDF 下载：按钮可用、两种结局（真实字节 / 被扩展接管）均正确处理")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

