# -*- coding: utf-8 -*-
"""清空并重建知识图谱（reset_data.py 的图谱部分单独执行，便于重跑）。"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


async def main() -> int:
    from sqlalchemy import select

    from app.db.models import SpecDoc
    from app.db.session import session_scope
    from app.kg.extractor import build_graph_for_doc, link_intra_doc_clauses
    from app.kg.graph_store import get_graph_store

    store = get_graph_store()
    print(f"  available: {store.available}")
    if not store.available:
        print("  [X] Neo4j 不可用，无法重建图谱")
        return 1

    print(f"  清空前: {store.stats()}")
    store.clear()
    print(f"  清空后: {store.stats()}")

    store.init_schema()

    with session_scope() as session:
        docs = session.execute(select(SpecDoc)).scalars().all()
        targets = [(d.id, d.spec_code) for d in docs]

    print(f"  待抽取规范：{len(targets)} 部")
    ok = 0
    for doc_id, spec_code in targets:
        try:
            with session_scope() as session:
                result = await build_graph_for_doc(session, doc_id)
                link_intra_doc_clauses(session, doc_id)
                session.commit()
            clauses = result.get("clauses", "?") if isinstance(result, dict) else "?"
            refs = result.get("references", "?") if isinstance(result, dict) else "?"
            print(f"    [OK] {spec_code:16s} clauses={clauses} refs={refs}")
            ok += 1
        except Exception as exc:  # noqa: BLE001
            print(f"    [警告] {spec_code} 失败：{type(exc).__name__}: {str(exc)[:120]}")

    print()
    print(f"  重建后: {store.stats()}")
    print(f"  成功 {ok}/{len(targets)} 部")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
