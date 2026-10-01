# -*- coding: utf-8 -*-
"""一次性数据迁移：把仓库根 var/ 下的运行时数据搬到 backend/var/。

背景：`app.config.PROJECT_ROOT` 早期误算为仓库根，导致 FAISS/BM25 索引与原始文件
落在 `<repo>/var/`。修正为 `backend/` 后，需要把已有数据搬过去，否则索引找不到、
检索会静默返回空结果。

用法：
    python scripts/migrate_var.py            # 预览（不落盘）
    python scripts/migrate_var.py --apply    # 执行迁移
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
REPO_ROOT = BACKEND.parent
OLD_ROOT = REPO_ROOT / "var"
NEW_ROOT = BACKEND / "var"

#: 只迁移运行时数据；artifacts（截图）留在仓库根，便于查看
MOVE_DIRS = ("faiss", "storage", "seed")
MOVE_FILES = ("project.db",)


def human(size: int) -> str:
    return f"{size / 1024:.1f} KB" if size < 1024 * 1024 else f"{size / 1024 / 1024:.2f} MB"


def dir_size(path: Path) -> tuple[int, int]:
    files = [p for p in path.rglob("*") if p.is_file()]
    return len(files), sum(p.stat().st_size for p in files)


def main() -> int:
    parser = argparse.ArgumentParser(description="迁移 var 运行时数据")
    parser.add_argument("--apply", action="store_true", help="实际执行迁移（默认仅预览）")
    args = parser.parse_args()

    print(f"源目录：{OLD_ROOT}")
    print(f"目标  ：{NEW_ROOT}\n")

    if not OLD_ROOT.exists():
        print("[SKIP] 源目录不存在，无需迁移")
        return 0

    plan: list[tuple[Path, Path, int, int]] = []
    for name in MOVE_DIRS:
        src = OLD_ROOT / name
        if src.exists() and any(src.iterdir()):
            count, size = dir_size(src)
            plan.append((src, NEW_ROOT / name, count, size))
    for name in MOVE_FILES:
        src = OLD_ROOT / name
        if src.exists():
            plan.append((src, NEW_ROOT / name, 1, src.stat().st_size))

    if not plan:
        print("[SKIP] 没有需要迁移的数据")
        return 0

    print(f"{'来源':<28}{'目标':<32}{'文件':>6}{'大小':>12}")
    print("-" * 80)
    for src, dst, count, size in plan:
        print(f"{src.relative_to(REPO_ROOT)!s:<28}{dst.relative_to(REPO_ROOT)!s:<32}{count:>6}{human(size):>12}")

    if not args.apply:
        print("\n[DRY-RUN] 未执行任何改动；加 --apply 执行迁移")
        return 0

    NEW_ROOT.mkdir(parents=True, exist_ok=True)
    for src, dst, _count, _size in plan:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            # 目标已存在：以目标为准，把源目录改名保留以便人工核对
            backup = dst.with_name(dst.name + ".old")
            if backup.exists():
                shutil.rmtree(backup, ignore_errors=True)
            shutil.move(str(src), str(backup))
            print(f"[KEEP] 目标已存在，源数据保留为 {backup.relative_to(REPO_ROOT)}")
            continue
        shutil.move(str(src), str(dst))
        print(f"[OK] {src.relative_to(REPO_ROOT)} → {dst.relative_to(REPO_ROOT)}")

    print("\n[DONE] 迁移完成。若服务正在运行请重启（索引在进程内存中缓存）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
