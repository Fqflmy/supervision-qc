# -*- coding: utf-8 -*-
"""项目结构统计：代码行数、模块分布、交付清单。"""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]

GROUPS = {
    "后端 · 核心层": ["backend/app/core", "backend/app/config.py", "backend/app/constants.py", "backend/app/db"],
    "后端 · AI 引擎": ["backend/app/llm", "backend/app/ingest", "backend/app/retrieval", "backend/app/kg", "backend/app/agent", "backend/app/judge"],
    "后端 · 接口层": ["backend/app/api", "backend/app/schemas", "backend/app/services", "backend/app/main.py"],
    "后端 · 测试": ["backend/tests"],
    "后端 · 脚本": ["backend/scripts"],
    "前端": ["frontend/src"],
    "部署编排": ["deploy"],
}


def count(paths: list[str], suffixes: tuple[str, ...]) -> tuple[int, int]:
    files = 0
    lines = 0
    for rel in paths:
        target = ROOT / rel
        if target.is_file():
            candidates = [target]
        else:
            candidates = [p for p in target.rglob("*") if p.is_file() and p.suffix in suffixes]
        for path in candidates:
            if path.suffix not in suffixes:
                continue
            if "__pycache__" in path.parts:
                continue
            files += 1
            try:
                lines += len(path.read_text(encoding="utf-8", errors="ignore").splitlines())
            except OSError:
                pass
    return files, lines


def main() -> int:
    print(f"{'模块':<20}{'文件':>6}{'行数':>9}")
    print("-" * 36)
    total_files = total_lines = 0
    for name, paths in GROUPS.items():
        suffixes = (
            (".ts", ".vue", ".css")
            if name == "前端"
            else (".yml", ".yaml", ".sql", ".conf", ".mjs", ".json")
            if name == "部署编排"
            else (".py",)
        )
        files, lines = count(paths, suffixes)
        total_files += files
        total_lines += lines
        print(f"{name:<20}{files:>6}{lines:>9}")
    print("-" * 36)
    print(f"{'合计':<20}{total_files:>6}{total_lines:>9}")

    print("\n=== 交付清单 ===")
    highlights = [
        "backend/app/retrieval/pipeline.py",
        "backend/app/agent/graph.py",
        "backend/app/agent/guards.py",
        "backend/app/agent/runner.py",
        "backend/app/judge/scorer.py",
        "backend/app/judge/citation.py",
        "backend/app/kg/graph_store.py",
        "backend/app/kg/extractor.py",
        "backend/app/ingest/splitter.py",
        "backend/app/llm/gateway.py",
        "backend/app/main.py",
        "frontend/src/api/index.ts",
        "frontend/src/views/EvalDetailView.vue",
        "frontend/src/views/ChatView.vue",
        "deploy/docker-compose.yml",
        "README.md",
    ]
    for rel in highlights:
        path = ROOT / rel
        mark = "OK " if path.exists() else "MISS"
        size = f"{len(path.read_text(encoding='utf-8', errors='ignore').splitlines())} 行" if path.exists() else "-"
        print(f"  [{mark}] {rel:<46} {size}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
