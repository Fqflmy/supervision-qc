# -*- coding: utf-8 -*-
"""检索管道自检：Multi-Query + BM25/向量混合 + RRF + Rerank + 图谱增强。"""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.logging_conf import setup_logging  # noqa: E402
from app.db import session_scope  # noqa: E402
from app.retrieval.embedding import get_embedder  # noqa: E402
from app.retrieval.pipeline import RetrievalPipeline  # noqa: E402
from app.retrieval.reranker import get_reranker  # noqa: E402

QUERIES = [
    "混凝土浇筑入模温度要求是多少",
    "脚手架连墙件怎么设置",
    "灌注桩沉渣厚度要求",
    "检验批合格的标准是什么",
]


async def main() -> int:
    setup_logging("WARNING", json_output=False)
    print(f"Embedder: {get_embedder().name} (degraded={get_embedder().degraded})")
    print(f"Reranker: {get_reranker().name} (degraded={get_reranker().degraded})")
    pipeline = RetrievalPipeline(namespace="kb_3")

    with session_scope() as session:
        for query in QUERIES:
            result = await pipeline.retrieve(query, session, top_k=3)
            print(f"\n=== {query}")
            print(f"  子查询({len(result.sub_queries)}): {result.sub_queries}")
            print(
                f"  耗时 {result.latency_ms}ms | 召回融合 {result.debug['recall']} | "
                f"无依据={result.no_evidence} | 图谱扩展={result.debug['kg_expanded']}"
            )
            for idx, clause in enumerate(result.clauses, start=1):
                print(
                    f"  [{idx}] score={clause.relevance_score:.3f} rrf={clause.rrf_score:.5f} "
                    f"{clause.citation.label()} | {clause.content[:60].replace(chr(10), ' ')}"
                )
            if result.clauses:
                top = result.clauses[0]
                assert top.citation.chunk_id, "引用溯源缺失 chunk_id"
                print(f"  溯源: {top.citation.render()}")
    print("\n[OK] 检索管道自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
