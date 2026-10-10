# -*- coding: utf-8 -*-
"""Windows 启动脚本的编码契约检查。

为什么需要这个检查
------------------
**PowerShell 5.1 读取 ``.ps1`` 时，若文件没有 UTF-8 BOM，会按系统 ANSI（简体中文
Windows 上是 GBK）解码。** 于是文件里的中文被错误解码，部分多字节序列会解出
``'``（0x27）或 ``"``（0x22），**提前终结字符串字面量**，脚本报
``The string is missing the terminator`` 并级联失败。

真实现象（本项目的启动脚本一度如此）：
    文件内容 ``Write-Host '  停止服务'`` 按 UTF-8 是正确的，
    但按 GBK 解成 ``Write-Host '  鍋滄㈡湇鍔�'`` —— 其中出现了 ``'``，
    字符串在中文中间就结束了，后面整段语法崩掉。
    报错位置在**毫不相关**的行（如 ``if ($Down) {``），极难定位。

``cmd`` 读 ``.bat`` 有同类问题，且 **cmd 不识别 LF 换行** ——
用 LF 保存的 ``.bat`` 会被当成一整行执行，``rem`` 注释后的内容会被
当作命令去跑（报 ``'xxx' is not recognized as an internal or external command``）。

这两个问题的共同点是：**在 UTF-8 的编辑器/终端里看不出任何异常**，
只有实际双击运行或换一台机器才暴露。因此必须用脚本守住。

检查规则
--------
1. ``*.ps1`` / ``*.bat`` / ``*.cmd``：含非 ASCII 字符时**必须有 UTF-8 BOM**；
2. ``*.bat`` / ``*.cmd``：**必须 CRLF** 换行（cmd 不认 LF）；
3. ``*.ps1``：建议 CRLF（``.gitattributes`` 已强制，此处一并校验）；
4. ``*.sh``：**必须没有 BOM**（bash 会把 BOM 当成命令的一部分报错）。

用法：
    python backend/scripts/check_script_encoding.py
"""
from __future__ import annotations

import sys
from pathlib import Path

BOM = b"\xef\xbb\xbf"

#: 需要检查的目录（仓库根 + 这些子目录）
SCAN_ROOTS = (".", "scripts", "deploy", "backend", "frontend")
SKIP_PARTS = ("node_modules", ".git", "var", "dist", "__pycache__", ".venv")

#: 扩展名 → 规则
RULES: dict[str, dict[str, bool]] = {
    ".ps1": {"require_bom_if_cjk": True, "require_crlf": True},
    ".bat": {"require_bom_if_cjk": True, "require_crlf": True},
    ".cmd": {"require_bom_if_cjk": True, "require_crlf": True},
    ".sh": {"forbid_bom": True},
}


def _has_cjk(data: bytes) -> bool:
    """是否含非 ASCII 字节（中文注释等）。"""
    return any(b > 127 for b in data)


def _iter_files(root: Path):
    seen: set[Path] = set()
    for rel in SCAN_ROOTS:
        base = root / rel
        if not base.exists():
            continue
        for pattern in ("*.ps1", "*.bat", "*.cmd", "*.sh"):
            for path in base.glob(pattern):
                if any(part in path.parts for part in SKIP_PARTS):
                    continue
                if path in seen:
                    continue
                seen.add(path)
                yield path


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    failures: list[str] = []
    checked = 0

    print("=== Windows / Shell 启动脚本编码契约 ===")
    for path in sorted(_iter_files(root)):
        rel = path.relative_to(root)
        rules = RULES.get(path.suffix.lower())
        if rules is None:
            continue
        checked += 1
        data = path.read_bytes()
        has_bom = data.startswith(BOM)
        cjk = _has_cjk(data)
        crlf = data.count(b"\r\n")
        bare_lf = data.count(b"\n") - crlf
        problems: list[str] = []

        if rules.get("require_bom_if_cjk") and cjk and not has_bom:
            problems.append(
                "缺少 UTF-8 BOM —— PowerShell 5.1 / cmd 会按 GBK 解码中文，"
                "可能提前终结字符串导致语法崩坏"
            )
        if rules.get("forbid_bom") and has_bom:
            problems.append("含 BOM —— bash 会把 BOM 当命令的一部分而报错")
        if rules.get("require_crlf") and bare_lf:
            problems.append(f"存在 {bare_lf} 个 LF 换行 —— cmd 不识别 LF，会把文件当一行执行")

        flag = "OK" if not problems else "需修"
        detail = f"BOM={has_bom} CRLF={crlf} LF={bare_lf} 非ASCII={cjk}"
        print(f"  [{flag:3s}] {str(rel):34s} {detail}")
        for p in problems:
            print(f"          → {p}")
            failures.append(f"{rel}: {p}")

    print()
    print(f"  共检查 {checked} 个脚本文件")
    if failures:
        print(f"[FAIL] {len(failures)} 处编码契约违规：")
        for f in failures:
            print(f"  {f}")
        print()
        print("  修复：")
        print("    PowerShell 脚本加 BOM：")
        print("      $c = Get-Content -Raw x.ps1; "
              "Set-Content -Encoding UTF8 x.ps1 $c   # PS5.1 的 UTF8 会带 BOM")
        print("    转 CRLF：用支持 CRLF 的编辑器另存，或")
        print("      (Get-Content x.bat) | Set-Content -Encoding UTF8 x.bat")
        return 1
    print("[OK] 全部脚本的 BOM 与换行符合契约")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
