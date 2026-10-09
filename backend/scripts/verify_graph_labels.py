# -*- coding: utf-8 -*-
"""图谱标签隔离与一致性验收（容器内执行，Neo4j 可用）。

对应 tests/test_graph_labels.py 的容器版本 —— 镜像里不含 tests/，
因此用脚本形式在容器内验证同样的断言。

用法：docker compose exec api python scripts/verify_graph_labels.py
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

passed = 0
failed = 0


def check(label: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"  [OK] {label}{('  ' + detail) if detail else ''}")
    else:
        failed += 1
        print(f"  [X ] {label}  {detail}")


def main() -> int:
    from sqlalchemy import func, select

    from app.db.models import SpecDoc
    from app.db.session import session_scope
    from app.kg.graph_store import RESERVED_NODE_LABELS, get_graph_store

    store = get_graph_store()
    if not store.available:
        print("  Neo4j 不可用，无法验收")
        return 1

    print("=== 1) 保留标签已声明 ===")
    check(
        "Spec / Clause / Chapter 声明为保留标签",
        {"Spec", "Clause", "Chapter"} <= RESERVED_NODE_LABELS,
        str(sorted(RESERVED_NODE_LABELS)),
    )

    print()
    print("=== 2) 实体不得占用保留标签 ===")
    for label in sorted(RESERVED_NODE_LABELS):
        text = f"__verify_保留标签_{label}__"
        try:
            store.upsert_entity(text, label)
            rows = store._run(f"MATCH (e:Entity {{text: '{text}'}}) RETURN labels(e) AS labels")
            labels = rows[0]["labels"] if rows else []
            leaked = [lbl for lbl in labels if lbl in RESERVED_NODE_LABELS]
            check(f"实体 label={label} 已降级", not leaked, f"节点标签={labels}")
        finally:
            store._run(f"MATCH (e:Entity {{text: '{text}'}}) DELETE e")

    print()
    print("=== 3) 普通标签应保留（不误降级）===")
    text = "__verify_普通标签__"
    try:
        store.upsert_entity(text, "Material")
        rows = store._run(f"MATCH (e:Entity {{text: '{text}'}}) RETURN labels(e) AS labels")
        labels = rows[0]["labels"] if rows else []
        check("实体 label=Material 保留", "Material" in labels, f"节点标签={labels}")
    finally:
        store._run(f"MATCH (e:Entity {{text: '{text}'}}) DELETE e")

    print()
    print("=== 4) 图谱 Spec 节点数与库中规范数一致 ===")
    with session_scope() as session:
        db_specs = int(session.execute(select(func.count(SpecDoc.id))).scalar() or 0)
    stats = store.stats()
    graph_specs = int(stats.get("specs", 0))
    check(
        f"图谱 Spec={graph_specs} 与库中规范={db_specs} 一致",
        graph_specs == db_specs,
        "" if graph_specs == db_specs else "很可能实体又占用了 Spec 标签",
    )

    print()
    print("=== 5) 图谱非空（重建是否真的写入）===")
    check("Clause 节点数 > 0", int(stats.get("clauses", 0)) > 0, f"clauses={stats.get('clauses')}")

    print()
    print("=" * 56)
    print(f"通过 {passed} 项，失败 {failed} 项")
    if failed:
        return 1
    print("[OK] 图谱标签隔离与一致性全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
