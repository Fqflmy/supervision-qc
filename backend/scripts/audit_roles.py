# -*- coding: utf-8 -*-
"""逐角色探测实际可用的功能（权限边界 + 是否「有权限但没入口」）。

目的：验证「功能是否合理、是否完善」这类问题不能只看权限表 ——
要看**每个角色实际能走通哪些流程**，以及是否存在
「接口允许但界面没有入口」或「界面有按钮但接口拒绝」的错位。

用法（需服务已启动）：
    python scripts/audit_roles.py
    python scripts/audit_roles.py --base http://127.0.0.1:8080
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

#: 探测项：(标签, 方法, 路径, 请求体)
#: 请求体必须**合法**，否则会拿到 422（参数不足）而误判成权限问题 ——
#: 这是上一版审计踩过的坑：用空 body 探测，422 被误读为「不可用」。
PROBES: list[tuple[str, str, str, dict | None]] = [
    ("查看我的信息", "GET", "/api/v1/auth/me", None),
    ("知识库列表", "GET", "/api/v1/kb", None),
    ("文档列表", "GET", "/api/v1/kb/documents", None),
    ("上传规范", "POST", "/api/v1/kb/documents", None),  # multipart，422 属预期
    ("图谱统计", "GET", "/api/v1/kb/kg/stats", None),
    ("图谱抽取", "POST", "/api/v1/kb/kg/extract", {"doc_id": 1}),
    ("混合检索", "POST", "/api/v1/retrieval/search", {"query": "混凝土强度", "top_k": 3}),
    ("智能问答", "POST", "/api/v1/retrieval/chat", {"query": "混凝土强度"}),
    ("任务列表", "GET", "/api/v1/eval/tasks", None),
    (
        "创建任务",
        "POST",
        "/api/v1/eval/tasks",
        {
            "eval_type": "inspection_lot",
            "specialty": "结构工程",
            "title": "角色审计探测",
            "object": {"part": "现浇混凝土结构", "item": "混凝土强度"},
        },
    ),
    ("评审看板", "GET", "/api/v1/judge/dashboard", None),
    ("用户管理", "GET", "/api/v1/admin/users", None),
    ("审计日志", "GET", "/api/v1/admin/audit-logs", None),
    ("运行配置", "GET", "/api/v1/admin/config", None),
    ("运行指标", "GET", "/api/v1/metrics", None),
    ("健康检查", "GET", "/api/v1/health", None),
    ("我的访问范围", "GET", "/api/v1/me/scope", None),
]

ROLES = [
    ("admin", "系统管理员"),
    ("kb_manager", "知识库管理员"),
    ("engineer", "监理工程师"),
    ("expert", "审核人员"),
    ("viewer", "普通用户"),
]

PASSWORD = "Test@12345"


def call(base: str, method: str, path: str, token: str | None, payload: dict | None) -> int:
    """返回状态码；网络异常返回 0。"""
    url = f"{base}{path}"
    body = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=body, method=method)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status
    except urllib.error.HTTPError as exc:
        return exc.code
    except Exception:  # noqa: BLE001
        return 0


def ensure_accounts() -> None:
    """确保 5 个角色的账号都存在（kb_manager 种子未创建，这里补上）。"""
    from app.core.security import hash_password
    from app.db.models import Project, User
    from app.db.session import session_scope

    with session_scope() as session:
        project = session.query(Project).order_by(Project.id).first()
        pid = project.id if project else None
        for role, label in ROLES:
            username = f"audit_{role}"
            row = session.query(User).filter(User.username == username).one_or_none()
            if row is None:
                session.add(
                    User(
                        username=username,
                        full_name=label,
                        password_hash=hash_password(PASSWORD),
                        role=role,
                        project_ids=[pid] if (pid and role != "admin") else [],
                    )
                )
            elif pid and role != "admin" and not (row.project_ids or []):
                row.project_ids = [pid]


def login(base: str, username: str) -> str | None:
    payload = json.dumps({"username": username, "password": PASSWORD}).encode()
    req = urllib.request.Request(f"{base}/api/v1/auth/login", data=payload, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            return json.loads(resp.read().decode())["data"]["access_token"]
    except Exception:  # noqa: BLE001
        return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", default="http://127.0.0.1:8080")
    args = parser.parse_args()
    base = args.base.rstrip("/")

    print("=== 准备审计账号 ===")
    ensure_accounts()
    print(f"  已确保 {len(ROLES)} 个角色账号存在")

    tokens: dict[str, str | None] = {}
    for role, _ in ROLES:
        tokens[role] = login(base, f"audit_{role}")
    missing = [r for r, t in tokens.items() if not t]
    if missing:
        print(f"  [X] 以下账号登录失败：{missing}")
        return 1
    print("  全部角色登录成功")
    print()

    # 表头
    header = f"  {'功能':14s}" + "".join(f"{label:>8s}" for _, label in
                                          [(r, l[:6]) for r, l in ROLES])
    print("=== 各角色接口访问结果（200/403/404/422 表示可达，401 未登录）===")
    print(f"  {'功能':16s}" + "".join(f"{l[:6]:>9s}" for _, l in ROLES))
    print("  " + "-" * (16 + 9 * len(ROLES)))

    results: dict[str, dict[str, int]] = {}
    for label_, method, path, payload in PROBES:
        row = {}
        cells = []
        for role, _ in ROLES:
            code = call(base, method, path, tokens[role], payload)
            row[role] = code
            # 2xx 记为「可」，403 记为「禁」，其它为「可达但参数/业务限制」
            mark = "OK" if 200 <= code < 300 else ("禁" if code == 403 else str(code))
            cells.append(f"{mark:>9s}")
        results[label_] = row
        print(f"  {label_:16s}" + "".join(cells))

    print()
    print("=== 解读 ===")
    print("  OK   = 该角色可访问（含参数不足但鉴权通过的 422/400）")
    print("  禁   = 403，权限不足（预期内）")
    print("  其它 = HTTP 状态码（多为缺少必填参数，说明鉴权已通过）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
