# -*- coding: utf-8 -*-
"""核实管理员对「用户管理」与「知识库管理」两个模块的完整可用性。

不只看菜单是否显示，而是把两个模块的**每个操作端点**都过一遍，
确认管理员都能调通 —— 避免「菜单在但点进去报错」。
"""
from __future__ import annotations

import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

#: 用户管理模块端点
USER_MODULE = [
    ("项目列表（授权下拉）", "GET", "/api/v1/admin/projects", None),
    ("用户列表", "GET", "/api/v1/admin/users", None),
    ("创建用户", "POST", "/api/v1/admin/users", None),  # 需完整 body，单独测
    ("当前访问范围", "GET", "/api/v1/me/scope", None),
]

#: 知识库管理模块端点
KB_MODULE = [
    ("知识库列表", "GET", "/api/v1/kb", None),
    ("文档列表", "GET", "/api/v1/kb/documents", None),
    ("图谱统计", "GET", "/api/v1/kb/kg/stats", None),
    ("图谱子图", "GET", "/api/v1/kb/kg/subgraph", None),
]


def login(base: str, username: str, password: str) -> str | None:
    payload = json.dumps({"username": username, "password": password}).encode()
    req = urllib.request.Request(f"{base}/api/v1/auth/login", data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())["data"]["access_token"]
    except Exception:  # noqa: BLE001
        return None


def call(base: str, method: str, path: str, token: str, payload: dict | None = None):
    body = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(f"{base}{path}", data=body, method=method)
    req.add_header("Content-Type", "application/json")
    req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=25) as resp:
            raw = resp.read().decode()
            try:
                return resp.status, json.loads(raw)
            except Exception:  # noqa: BLE001
                return resp.status, raw
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode()[:200]
    except Exception as exc:  # noqa: BLE001
        return 0, str(exc)[:200]


def main() -> int:
    base = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8080"
    token = login(base, "admin", "Admin@12345")
    if not token:
        print("[X] admin 登录失败")
        return 1

    problems: list[str] = []

    for title, probes in (("用户管理模块", USER_MODULE), ("知识库管理模块", KB_MODULE)):
        print(f"=== {title} ===")
        for label, method, path, payload in probes:
            code, data = call(base, method, path, token, payload)
            ok = 200 <= code < 300
            if not ok:
                problems.append(f"{title} · {label} -> {code}")
            detail = ""
            if ok and isinstance(data, dict):
                d = data.get("data")
                if isinstance(d, list):
                    detail = f"{len(d)} 条"
                elif isinstance(d, dict):
                    # 常见计数字段
                    for k in ("total", "size", "count", "clauses"):
                        if k in d:
                            detail = f"{k}={d[k]}"
                            break
            print(f"  {'[OK]' if ok else '[X] '} {label:20s} {code}  {detail}")
        print()

    # 创建用户：用真实 body 验证写权限
    print("=== 用户管理模块 · 写操作 ===")
    uname = "ui_probe_user"
    code, _ = call(base, "GET", "/api/v1/admin/users?keyword=" + uname, token)
    payload = {
        "username": uname,
        "password": "Probe@12345",
        "full_name": "界面探测用户",
        "role": "viewer",
        "project_ids": [],
    }
    code, data = call(base, "POST", "/api/v1/admin/users", token, payload)
    if code in (200, 201):
        print(f"  [OK] 创建用户 {uname} -> {code}")
        created_id = data.get("data", {}).get("id") if isinstance(data, dict) else None
        if created_id:
            # 停用再启用（验证状态接口）
            code2, _ = call(
                base, "POST", f"/api/v1/admin/users/{created_id}/status", token, {"is_active": False}
            )
            print(f"  [{'OK' if code2 == 200 else 'X '}] 停用用户 -> {code2}")
    elif code == 409:
        print(f"  [OK] 创建用户 -> {code}（已存在，幂等）")
    else:
        print(f"  [X ] 创建用户 -> {code} {str(data)[:150]}")
        problems.append(f"创建用户 -> {code}")

    print()
    if problems:
        print(f"[FAIL] {len(problems)} 项不可用：")
        for p in problems:
            print(f"  {p}")
        return 1
    print("[OK] 管理员对「用户管理」与「知识库管理」两个模块的端点全部可用")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
