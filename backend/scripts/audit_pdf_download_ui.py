# -*- coding: utf-8 -*-
"""报告 PDF 下载的浏览器验收：真实点击下载并检查文件落盘。

为什么必须做浏览器验收
----------------------
后端接口测试只能证明「接口能返回 PDF」，证明不了：
- 按钮是否真的可见、可点（不是被权限隐藏或被 CSS 遮挡）；
- 前端是否正确处理**二进制响应**（用普通 GET 会把 PDF 当 JSON 解析而失败）；
- 浏览器是否真的把文件存到磁盘、文件名是否正确（含中文）。

因此本脚本用 Playwright 的 download 事件拦截真实下载并读取文件内容。

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
    download_dir = ARTIFACTS / "pdf-downloads"
    download_dir.mkdir(parents=True, exist_ok=True)
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
            viewport={"width": 1600, "height": 1000},
            locale="zh-CN",
            accept_downloads=True,
        )
        page = context.new_page()
        errors: list[str] = []
        page.on("pageerror", lambda exc: errors.append(str(exc)[:200]))

        # ---------- 1) 报告列表页下载 ----------
        print("=== 1) 评估报告列表：下载按钮 ===")
        _login(page, base, "普通用户")
        page.goto(f"{base}/#/reports", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(3000)

        dl_btn = page.locator('button:has-text("下载")')
        check("列表页有下载按钮", dl_btn.count() > 0, f"{dl_btn.count()} 个")

        if dl_btn.count() > 0:
            with page.expect_download(timeout=120000) as dl_info:
                dl_btn.first.click()
            download = dl_info.value
            target = download_dir / "list-download.pdf"
            download.save_as(str(target))

            check("触发了下载", True, download.suggested_filename)
            check("文件名以 .pdf 结尾", download.suggested_filename.endswith(".pdf"),
                  download.suggested_filename)
            check("文件名含中文（RFC 5987 生效）",
                  any("\u4e00" <= ch <= "\u9fff" for ch in download.suggested_filename),
                  download.suggested_filename)
            check("文件已落盘", target.exists(), str(target.stat().st_size if target.exists() else 0))
            if target.exists():
                head = target.read_bytes()[:5]
                check("落盘内容是 PDF", head == b"%PDF-", repr(head))
                check("体积 > 10KB", target.stat().st_size > 10_000,
                      f"{target.stat().st_size} 字节")

            page.wait_for_timeout(2500)
            body = page.inner_text("body")
            # 已签发的报告应提示成功
            check("有下载结果提示", "已下载" in body or "报告已下载" in body)

        page.screenshot(path=str(ARTIFACTS / "30-pdf-list.png"), full_page=True)

        # ---------- 2) 报告详情页下载 ----------
        print()
        print("=== 2) 报告详情：下载 PDF ===")
        page.goto(f"{base}/#/reports", wait_until="networkidle", timeout=60000)
        page.wait_for_timeout(2500)
        page.locator("button:has-text('查看报告')").first.click()
        page.wait_for_timeout(3500)

        detail_btn = page.locator('button:has-text("下载 PDF")')
        check("详情页有「下载 PDF」按钮", detail_btn.count() > 0)

        if detail_btn.count() > 0:
            with page.expect_download(timeout=120000) as dl2_info:
                detail_btn.first.click()
            dl2 = dl2_info.value
            target2 = download_dir / "detail-download.pdf"
            dl2.save_as(str(target2))
            check("详情页下载成功", target2.exists())
            if target2.exists():
                check("内容是 PDF", target2.read_bytes()[:5] == b"%PDF-")
                check("两处下载内容一致（同一份报告）",
                      target2.stat().st_size == target.stat().st_size
                      if target.exists() else True,
                      f"{target2.stat().st_size} vs "
                      f"{target.stat().st_size if target.exists() else '?'}")

        page.screenshot(path=str(ARTIFACTS / "30-pdf-detail.png"), full_page=True)

        # ---------- 3) 权限：只读用户不该出现写操作 ----------
        print()
        print("=== 3) 只读用户界面检查 ===")
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
    print(f"下载文件保存在：{download_dir}")
    if failures:
        for f in failures:
            print(f"  失败：{f}")
        return 1
    print("[OK] PDF 下载：列表与详情两处均可用，文件名与内容正确")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
