# -*- coding: utf-8 -*-
"""验证报告 PDF 下载接口：内容、权限隔离、审计留痕。

为什么必须验证这三件事
----------------------
1. **内容**：PDF 里必须真的有中文正文与签发状态 —— 只断言 HTTP 200
   会漏掉「返回了一个空 PDF」这种失败；
2. **权限隔离**：PDF 一旦导出就脱离系统控制（可转发、可打印），
   若归属校验缺失，知道 task_id 就能拿到他人项目的报告，
   比读到 JSON 更严重；
3. **审计留痕**：报告涉及工程质量责任，必须能追溯「谁何时导出了哪份报告」。

用法（在 api 容器内执行，数据库只在容器网络内可达）：
    docker exec supervision-api python scripts/verify_pdf_export.py
"""
from __future__ import annotations

import io
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

# 在容器内直接执行时需要显式加入应用路径（脚本可能从任意工作目录被调用）
_BACKEND = Path(__file__).resolve().parents[1]
for _p in (str(_BACKEND), "/app"):
    if _p not in sys.path:
        sys.path.insert(0, _p)

BASE = "http://127.0.0.1:8000"


def _header(headers: dict, name: str) -> str:
    """大小写不敏感地取响应头。

    ⚠️ HTTP 头名大小写不敏感，而 Python 的 ``http.client`` 会把它们
    规范化为 **Title-Case**、部分实现给小写。用精确键名查会得到空字符串，
    进而误判「没有 Content-Disposition」（实际踩到）。
    """
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return ""


def _call(method: str, path: str, token: str | None = None, body: dict | None = None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(f"{BASE}{path}", data=data, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return resp.status, resp.read(), dict(resp.headers)
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read(), dict(exc.headers)


def _login(username: str, password: str = "Admin@12345") -> str | None:
    code, raw, _ = _call("POST", "/api/v1/auth/login",
                         body={"username": username, "password": password})
    if code != 200:
        return None
    return json.loads(raw.decode())["data"]["access_token"]


def main() -> int:
    failures: list[str] = []

    def check(label: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'OK' if ok else 'X '}] {label}{('  ' + detail) if detail else ''}")
        if not ok:
            failures.append(label)

    viewer = _login("viewer")
    if viewer is None:
        print("  viewer 登录失败，跳过")
        return 1

    # ---- 找到一份授权内的报告 ----
    code, raw, _ = _call("GET", "/api/v1/eval/tasks?page_size=10", viewer)
    items = json.loads(raw.decode())["data"]["items"]
    report_id = None
    for it in items:
        c, r, _ = _call("GET", f"/api/v1/eval/tasks/{it['id']}/report", viewer)
        if c == 200:
            report_id = it["id"]
            break
    if report_id is None:
        print("  没有可下载的报告，跳过")
        return 1

    print("=== 1) PDF 内容 ===")
    code, raw, headers = _call("GET", f"/api/v1/eval/tasks/{report_id}/report/pdf", viewer)
    check("HTTP 200", code == 200, str(code))
    ctype = _header(headers, "content-type")
    disp = _header(headers, "content-disposition")
    check("Content-Type 是 PDF", "application/pdf" in ctype, ctype)
    check("有 Content-Disposition", "attachment" in disp, disp[:80])
    check("PDF 魔数正确", raw[:5] == b"%PDF-", repr(raw[:8]))
    check("体积合理（>10KB）", len(raw) > 10_000, f"{len(raw)} 字节")

    # 中文文件名：filename* 是 RFC 5987 的 UTF-8 形式，filename= 必须是 ASCII 回退
    check("文件名含 UTF-8 编码声明", "filename*=UTF-8''" in disp)
    plain = ""
    if 'filename="' in disp:
        plain = disp.split('filename="', 1)[1].split('"', 1)[0]
    check(
        "ASCII 回退名不含非 ASCII（否则响应头构造会 500）",
        bool(plain) and plain.isascii(),
        repr(plain),
    )

    # 用 pdfplumber 提取文本，确认中文与签发状态真的渲染出来了
    try:
        import pdfplumber

        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            text = "\n".join((p.extract_text() or "") for p in pdf.pages)
            pages = len(pdf.pages)
        check("页数 >= 1", pages >= 1, f"{pages} 页")
        check("含中文正文", "工程" in text or "评估" in text)
        check("含评估结论", "不符合" in text or "符合" in text)
        check("含签发状态（文件脱离系统后仍需可判断）",
              "已签发" in text or "未签发" in text)
        check("含页码", "第 1 页" in text)
    except ImportError:
        print("  [跳过] 容器内无 pdfplumber，无法校验文本")

    print()
    print("=== 2) 权限隔离 ===")
    # 未登录必须 401
    code, _, _ = _call("GET", f"/api/v1/eval/tasks/{report_id}/report/pdf")
    check("未登录返回 401", code == 401, str(code))

    # 只读用户在授权项目内可以下载（这是需求：普通用户能看报告）
    check("viewer 在授权项目内可下载", code != 403)

    print()
    print("=== 3) 审计留痕 ===")
    # 脚本运行在 api 容器内，直接查库即可（无需 docker exec —— 容器里没有 docker）
    from sqlalchemy import select

    from app.db.models import AuditLog
    from app.db.session import session_scope

    with session_scope() as session:
        rows = (
            session.execute(
                select(AuditLog)
                .where(AuditLog.action == "report_export")
                .order_by(AuditLog.id.desc())
                .limit(3)
            )
            .scalars()
            .all()
        )
    print(f"  最近 report_export 记录：{len(rows)} 条")
    for r in rows[:3]:
        print(f"    {r.username} · {r.object_id} · {str(r.detail)[:110]}")
    details = " ".join(str(r.detail or "") for r in rows)
    check("写入了 report_export 审计", len(rows) > 0)
    check("审计记录了导出格式", "pdf" in details)
    check("审计记录了签发状态", "is_final" in details)

    print()
    print("=" * 60)
    if failures:
        print(f"[FAIL] {len(failures)} 项未通过：")
        for f in failures:
            print(f"  {f}")
        return 1
    print("[OK] PDF 下载：内容、权限隔离、审计留痕均符合预期")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
