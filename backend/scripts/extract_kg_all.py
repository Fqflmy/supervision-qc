# -*- coding: utf-8 -*-
"""批量抽取知识图谱：对全部已发布规范执行实体关系抽取，并写入 Neo4j。

等价于对每份文档调用一次 ``POST /api/v1/kb/kg/extract``，
但直接在进程内批量执行，避免长时间 HTTP 请求被网关超时截断。

用法：
    docker compose exec api python scripts/extract_kg_all.py
    docker compose exec api python scripts/extract_kg_all.py --limit 10   # 每份文档只抽前 10 条
    docker compose exec api python scripts/extract_kg_all.py --doc-id 3   # 只抽指定文档

也可在本机执行（需数据层可达）：
    python backend/scripts/extract_kg_all.py
"""
from __future__ import annotations

import argparse
import asyncio
import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import select  # noqa: E402

from app.constants import ChunkStatus, DocStatus  # noqa: E402
from app.core.logging_conf import setup_logging  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.db.models import DocChunk, SpecDoc  # noqa: E402


async def run(doc_ids: list[int] | None, limit: int | None, concurrency: int) -> int:
    setup_logging("WARNING", json_output=False)

    from app.kg.extractor import build_graph_for_doc, link_intra_doc_clauses
    from app.kg.graph_store import get_graph_store

    store = get_graph_store()
    if not store.available:
        print("[FAIL] Neo4j 不可用，无法构建图谱（检查 SUPERVISION_NEO4J_URI 与容器状态）")
        return 2
    store.init_schema()

    with session_scope() as session:
        stmt = select(SpecDoc).where(SpecDoc.status == DocStatus.PUBLISHED.value).order_by(SpecDoc.id)
        if doc_ids:
            stmt = stmt.where(SpecDoc.id.in_(doc_ids))
        docs = list(session.execute(stmt).scalars())

        if not docs:
            print("[FAIL] 没有已发布的规范文档，请先执行 seed_data.py")
            return 2

        print(f"待抽取文档 {len(docs)} 份，条款分块明细：")
        total_chunks = 0
        for doc in docs:
            count = len(
                session.execute(
                    select(DocChunk.id).where(
                        DocChunk.doc_id == doc.id,
                        DocChunk.status == ChunkStatus.ACTIVE.value,
                    )
                ).all()
            )
            total_chunks += count
            print(f"  #{doc.id} {doc.spec_code} {doc.spec_name[:30]} — {count} 条")
        print(f"合计 {total_chunks} 条，预计 {total_chunks * 3}-{total_chunks * 8} 秒\n")

        started = time.perf_counter()
        totals = {"clauses": 0, "terms": 0, "references": 0, "errors": 0}

        for index, doc in enumerate(docs, 1):
            print(f"[{index}/{len(docs)}] 抽取 {doc.spec_code} …", flush=True)
            t0 = time.perf_counter()
            try:
                result = await build_graph_for_doc(
                    session, doc.id, limit=limit, concurrency=concurrency
                )
                linked = link_intra_doc_clauses(session, doc.id)
                session.commit()
                for key in ("clauses", "terms", "references", "errors"):
                    totals[key] += int(result.get(key) or 0)
                print(
                    f"  完成 {time.perf_counter() - t0:.1f}s | "
                    f"实体={result.get('entities')} 关系={result.get('relations')} "
                    f"引用={result.get('references')} 失败={result.get('failed')} "
                    f"文内链接={linked}",
                    flush=True,
                )
            except Exception as exc:  # noqa: BLE001
                session.rollback()
                totals["failed"] += 1
                print(f"  失败 {time.perf_counter() - t0:.1f}s | {type(exc).__name__}: {str(exc)[:200]}", flush=True)

        elapsed = time.perf_counter() - started
        stats = store.stats()
        print(f"\n{'=' * 60}")
        print(f"抽取完成：耗时 {elapsed:.1f}s")
        print("累计写入：" + " ".join(f"{k}={v}" for k, v in totals.items()))
        print(f"图谱统计：{stats}")

        # 抽样验证引用链
        sample = session.execute(
            select(DocChunk.clause_no)
            .where(DocChunk.status == ChunkStatus.ACTIVE.value, DocChunk.clause_no.isnot(None))
            .limit(5)
        ).scalars().all()
        print("\n引用链抽样：")
        for clause_no in sample:
            chain = store.reference_chain(clause_no, direction="both", depth=2)
            edges = chain.get("edges") or chain.get("references") or []
            print(f"  {clause_no}: {len(edges)} 条关系")

    return 0 if stats.get("clauses", 0) > 0 else 3


def main() -> int:
    parser = argparse.ArgumentParser(description="批量抽取知识图谱")
    parser.add_argument("--doc-id", type=int, action="append", help="只抽取指定 doc_id（可重复）")
    parser.add_argument("--limit", type=int, default=None, help="每份文档最多抽取的条款数")
    parser.add_argument("--concurrency", type=int, default=4, help="并发度（1-16）")
    args = parser.parse_args()
    return asyncio.run(run(args.doc_id, args.limit, max(1, min(16, args.concurrency))))


if __name__ == "__main__":
    raise SystemExit(main())
