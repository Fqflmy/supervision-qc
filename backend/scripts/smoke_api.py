# -*- coding: utf-8 -*-
"""应用自检：导入、路由清单、OpenAPI 生成。"""
from __future__ import annotations

import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.main import app  # noqa: E402


def walk_routes(routes, prefix: str = "") -> list[tuple[str, str, str]]:
    """递归展开 include_router 生成的嵌套路由。"""
    out: list[tuple[str, str, str]] = []
    for route in routes:
        nested = getattr(route, "routes", None)
        sub_prefix = prefix + (getattr(route, "prefix", "") or "")
        if nested:
            out.extend(walk_routes(nested, sub_prefix))
            continue
        methods = getattr(route, "methods", None)
        path = getattr(route, "path", None)
        if methods and path:
            out.append(
                (
                    sorted(methods - {"HEAD", "OPTIONS"})[0],
                    path,
                    (getattr(route, "summary", "") or "")[:44],
                )
            )
    return out


def main() -> int:
    schema = app.openapi()
    paths = schema["paths"]
    print(f"API 前缀: {settings.api_prefix}  路径数: {len(paths)}")

    listed: list[tuple[str, str, str]] = []
    for path, operations in paths.items():
        for method, spec in operations.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            listed.append((method.upper(), path, (spec.get("summary") or "")[:44]))
    for method, path, summary in sorted(listed, key=lambda x: (x[1], x[0])):
        print(f"  {method:6s} {path:58s} {summary}")

    print(f"\nOpenAPI: paths={len(paths)} schemas={len(schema['components']['schemas'])}")

    # 校验关键接口存在（SRS 6.2 清单）
    required = [
        ("post", "/auth/login"),
        ("post", "/kb/documents"),
        ("post", "/kb/documents/{doc_id}/parse"),
        ("post", "/kb/documents/{doc_id}/publish"),
        ("post", "/kb/kg/extract"),
        ("get", "/kb/kg/clauses/{clause_no}/refs"),
        ("post", "/retrieval/search"),
        ("post", "/retrieval/chat"),
        ("post", "/eval/tasks"),
        ("get", "/eval/tasks/{task_id}"),
        ("post", "/eval/tasks/{task_id}/resume"),
        ("get", "/eval/tasks/{task_id}/report"),
        ("post", "/judge/reports/{report_id}/score"),
        ("get", "/judge/dashboard"),
        ("get", "/admin/audit-logs"),
        ("get", "/health"),
    ]
    paths = schema["paths"]
    missing = []
    for method, suffix in required:
        full = f"{settings.api_prefix}{suffix}"
        if full not in paths or method not in paths[full]:
            missing.append(f"{method.upper()} {full}")
    if missing:
        print("\n[FAIL] 缺失接口：")
        for item in missing:
            print("  -", item)
        return 1
    print("\n[OK] 全部关键接口已注册")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
