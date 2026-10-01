# -*- coding: utf-8 -*-
"""用当前 Embedding 模型全量重建检索索引（FAISS + BM25）。

适用场景：
- 最初用哈希降级向量建库，之后接入真实 Embedding，需要重建；
- 更换 Embedding 模型或维度；
- 索引文件损坏或与数据库不一致。

用法：
    python scripts/rebuild_index.py                    # 全量重建（仅已发布文档）
    python scripts/rebuild_index.py --batch-size 32    # 调整批次（默认取配置值）
    python scripts/rebuild_index.py --all-docs         # 含未发布文档（不推荐）
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

from app.config import settings  # noqa: E402
from app.constants import ChunkStatus, DocStatus  # noqa: E402
from app.core.logging_conf import setup_logging  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.db.models import DocChunk, SpecDoc  # noqa: E402
from app.retrieval.bm25 import Bm25Document, get_bm25_index  # noqa: E402
from app.retrieval.embedding import get_embedder  # noqa: E402
from app.retrieval.vector_store import discover_namespaces, get_faiss_index  # noqa: E402
from app.services.ingest import namespace_of  # noqa: E402


async def rebuild(batch_size: int, only_published: bool) -> int:
    setup_logging("INFO", json_output=False)
    embedder = get_embedder()
    print(f"Embedding: {embedder.name} (dim={embedder.dim}, degraded={embedder.degraded})")
    if embedder.degraded:
        print("[警告] 当前 Embedding 处于降级状态，重建后的索引质量有限")

    with session_scope() as session:
        stmt = (
            select(DocChunk, SpecDoc)
            .join(SpecDoc, DocChunk.doc_id == SpecDoc.id)
            .where(DocChunk.status == ChunkStatus.ACTIVE.value)
            .order_by(DocChunk.id)
        )
        if only_published:
            stmt = stmt.where(SpecDoc.status == DocStatus.PUBLISHED.value)
        rows = session.execute(stmt).all()

        if not rows:
            print("[FAIL] 没有可重建的分块；请确认已执行 seed_data.py 且文档为 published")
            return 2

        # 按知识库分片归组（与检索时的 namespace 逻辑保持一致）
        grouped: dict[str, list[tuple[DocChunk, SpecDoc]]] = {}
        for chunk, doc in rows:
            grouped.setdefault(namespace_of(doc), []).append((chunk, doc))

        print(f"待重建分块：{len(rows)} 个，分片：{list(grouped)}\n")

        total_started = time.perf_counter()
        total_embedded = 0

        for namespace, items in grouped.items():
            print(f"--- 分片 {namespace}（{len(items)} 个分块）---")
            # 1) 清空旧索引：用独立实例重建，避免旧向量残留
            old_ids = [int(chunk.id) for chunk, _ in items]
            get_faiss_index(namespace).remove(old_ids)
            get_bm25_index(namespace).remove(old_ids)

            faiss_index = get_faiss_index(namespace)
            bm25_index = get_bm25_index(namespace)

            # 2) 分批向量化并写入
            started = time.perf_counter()
            for start in range(0, len(items), batch_size):
                batch = items[start : start + batch_size]
                texts = [chunk.content for chunk, _ in batch]
                vectors = await asyncio.to_thread(embedder.encode, texts, is_query=False)
                ids = [int(chunk.id) for chunk, _ in batch]
                metas = [
                    {"clause_no": chunk.clause_no, "doc_id": int(chunk.doc_id), "version_id": int(chunk.doc_version_id)}
                    for chunk, _ in batch
                ]
                faiss_index.add(vectors, ids, metas)
                total_embedded += len(batch)
                done = min(start + batch_size, len(items))
                elapsed = time.perf_counter() - started
                rate = done / elapsed if elapsed > 0 else 0
                eta = (len(items) - done) / rate if rate > 0 else 0
                print(
                    f"  向量化 {done}/{len(items)}  用时 {elapsed:.1f}s  "
                    f"速度 {rate:.1f} 条/s  预计剩余 {eta:.0f}s",
                    flush=True,
                )

            # 3) BM25 一次性建好（BM25 统计依赖全量语料）
            bm25_index.add(
                [
                    Bm25Document(
                        chunk_id=int(chunk.id),
                        text=chunk.content,
                        clause_no=chunk.clause_no,
                        doc_id=int(chunk.doc_id),
                        meta={"chapter_path": chunk.chapter_path, "page_no": chunk.page_no},
                    )
                    for chunk, _ in items
                ]
            )

            faiss_index.save()
            bm25_index.save()
            print(f"  已保存：faiss={faiss_index.size}  bm25={bm25_index.size}\n")

        elapsed = time.perf_counter() - total_started
        print(f"[OK] 重建完成：{total_embedded} 个分块，耗时 {elapsed:.1f}s")
        print(f"     索引目录：{settings.faiss_index_dir}")
        print(f"     分片：{discover_namespaces()}")

    if embedder.degraded:
        print("\n[警告] Embedding 为降级模式，检索效果有限；建议配置真实 Embedding 后重跑本脚本")
        return 3
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="用当前 Embedding 模型全量重建检索索引")
    parser.add_argument("--batch-size", type=int, default=settings.embedding_batch_size)
    parser.add_argument("--all-docs", action="store_true", help="包含未发布文档（默认仅已发布）")
    args = parser.parse_args()
    return asyncio.run(rebuild(max(1, args.batch_size), only_published=not args.all_docs))


if __name__ == "__main__":
    raise SystemExit(main())
