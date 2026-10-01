# -*- coding: utf-8 -*-
"""验证硅基流动托管的 Embedding / Reranker 服务可用性与质量。

检查项：
1. Embedding 调用成功、维度与配置一致；
2. 语义相似度排序合理（相关句 > 无关句）；
3. Reranker 调用成功，且能把真正相关的条款排在前面。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.config import settings  # noqa: E402
from app.core.logging_conf import setup_logging  # noqa: E402
from app.retrieval.embedding import get_embedder  # noqa: E402
from app.retrieval.reranker import get_reranker  # noqa: E402


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = sum(x * x for x in a) ** 0.5
    nb = sum(y * y for y in b) ** 0.5
    return dot / (na * nb) if na and nb else 0.0


def main() -> int:
    setup_logging("WARNING", json_output=False)
    print(f"Embedding provider = {settings.embedding_provider} | model = {settings.embedding_model}")
    print(f"Reranker  provider = {settings.reranker_provider} | model = {settings.reranker_model}\n")

    failures = 0

    # ---------------- Embedding ----------------
    print("[1] Embedding 服务")
    started = time.perf_counter()
    try:
        embedder = get_embedder()
        print(f"  实现类: {type(embedder).__name__} | name={embedder.name} | degraded={embedder.degraded}")
        if embedder.degraded:
            print("  [FAIL] 仍处于降级状态，请检查 API Key 与网络")
            failures += 1
        texts = [
            "混凝土浇筑时的入模温度不宜高于30℃",
            "脚手架连墙件应从底层第一步纵向水平杆处开始设置",
            "楼板浇完混凝土太热了会不会有问题",
        ]
        vectors = embedder.encode(texts)
        elapsed = time.perf_counter() - started
        print(f"  向量数={len(vectors)} 维度={len(vectors[0])} 耗时={elapsed:.2f}s")
        assert len(vectors) == len(texts)
        assert len(vectors[0]) == settings.embedding_dim, (
            f"维度不一致：返回 {len(vectors[0])}，配置 {settings.embedding_dim}"
        )

        query_vector = embedder.encode_one("入模温度过高有什么影响", is_query=True)
        sims = [round(cosine(query_vector, v), 4) for v in vectors]
        print(f"  与查询「入模温度过高有什么影响」的余弦相似度：{sims}")
        # 语义检索应把温度相关句排在脚手架句之前
        if sims[0] > sims[1]:
            print("  [OK] 语义排序正确（温度句 > 脚手架句）")
        else:
            print("  [FAIL] 语义排序异常")
            failures += 1
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"  [FAIL] {type(exc).__name__}: {str(exc)[:300]}")

    # ---------------- Reranker ----------------
    print("\n[2] Reranker 服务")
    started = time.perf_counter()
    try:
        reranker = get_reranker()
        print(f"  实现类: {type(reranker).__name__} | name={reranker.name} | degraded={reranker.degraded}")
        if reranker.degraded:
            print("  [FAIL] 仍处于降级状态，请检查 API Key 与网络")
            failures += 1
        query = "混凝土浇筑入模温度要求"
        documents = [
            "5.3.3 混凝土浇筑时的入模温度不宜高于30℃，不宜低于5℃。",
            "6.4.1 连墙件必须采用可承受拉力和压力的构件，严禁使用仅有拉筋的柔性连墙件。",
            "5.3.4 混凝土浇筑完毕后应及时采取有效的养护措施，浇水养护时间不得少于7d。",
            "5.2.1 水泥进场时应对其品种、级别、出厂日期等进行检查并复验。",
        ]
        results = reranker.rerank(query, documents, top_n=4)
        elapsed = time.perf_counter() - started
        print(f"  耗时={elapsed:.2f}s")
        for item in results:
            print(f"    score={item.score:.4f}  idx={item.index}  {documents[item.index][:38]}...")
        if results and results[0].index == 0:
            print("  [OK] 最相关条款排在第一")
        else:
            print(f"  [FAIL] 排序异常，首位为 idx={results[0].index if results else 'N/A'}")
            failures += 1
        assert all(0.0 <= item.score <= 1.0 for item in results), "分数应在 0-1 之间"
    except Exception as exc:  # noqa: BLE001
        failures += 1
        print(f"  [FAIL] {type(exc).__name__}: {str(exc)[:300]}")

    print()
    if failures:
        print(f"[FAIL] {failures} 项未通过")
        return 1
    print("[OK] 硅基流动 Embedding / Reranker 验证通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
