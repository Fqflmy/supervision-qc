# -*- coding: utf-8 -*-
"""检索链路测试：RRF 融合、切分、Embedding、查询改写（SRS FR-RET）。"""
from __future__ import annotations

import pytest

from app.ingest.parser import ParsedBlock, clean_text
from app.ingest.splitter import split_blocks
from app.retrieval.bm25 import Bm25Document, Bm25Index, tokenize
from app.retrieval.embedding import HashEmbedder
from app.retrieval.fusion import RankedItem, normalize_scores, rrf_fuse, weighted_rrf
from app.retrieval.query import understand_query
from app.retrieval.vector_store import FaissIndex


# --------------------------------------------------------------------------- #
# RRF 融合（FR-RET-05）
# --------------------------------------------------------------------------- #
def test_rrf_favors_item_ranked_high_in_multiple_lists():
    bm25 = [RankedItem(2, 9.0, 1, "bm25"), RankedItem(1, 5.0, 2, "bm25")]
    dense = [RankedItem(2, 0.9, 1, "dense"), RankedItem(3, 0.8, 2, "dense")]
    fused = rrf_fuse([bm25, dense], k=60)
    assert fused[0].chunk_id == 2, "同时被两路召回且都排第一的条目应最高"
    assert set(fused[0].sources) == {"bm25", "dense"}
    # RRF 分值 = w/(k+rank) 之和
    expected = 1 / 61 + 1 / 61
    assert fused[0].rrf_score == pytest.approx(expected)


def test_rrf_weights_shift_ranking():
    """提升 BM25 权重应让 BM25 独占条目上浮。"""
    bm25 = [RankedItem(1, 5.0, 1, "bm25")]
    dense = [RankedItem(2, 0.9, 1, "dense")]
    fused = weighted_rrf({"bm25": bm25, "dense": dense}, {"bm25": 5.0, "dense": 0.1}, k=60)
    assert fused[0].chunk_id == 1


def test_rrf_dedupes_and_tracks_best_rank():
    lists = [
        [RankedItem(7, 1.0, 3, "bm25")],
        [RankedItem(7, 1.0, 1, "dense")],
    ]
    fused = rrf_fuse(lists, k=60)
    assert len(fused) == 1
    assert fused[0].best_rank == 1
    assert len(fused[0].sources) == 2


def test_rrf_top_k_truncation_and_empty_input():
    assert rrf_fuse([]) == []
    assert rrf_fuse([[]]) == []
    lists = [[RankedItem(i, 1.0, i, "x") for i in range(1, 11)]]
    assert len(rrf_fuse(lists, top_k=4)) == 4


def test_rrf_weight_length_mismatch_raises():
    with pytest.raises(ValueError):
        rrf_fuse([[RankedItem(1, 1.0, 1, "x")]], weights=[1.0, 2.0])


def test_normalize_scores_handles_constant_scores():
    items = [RankedItem(1, 5.0, 1, "a"), RankedItem(2, 5.0, 2, "a")]
    normalized = normalize_scores(items)
    assert all(item.score == 1.0 for item in normalized)


# --------------------------------------------------------------------------- #
# 切分（FR-KB-05）
# --------------------------------------------------------------------------- #
def test_splitter_preserves_clause_numbers():
    blocks = [
        ParsedBlock(text="第5章 混凝土分项工程", level=1, page_no=10),
        ParsedBlock(text="5.3 混凝土施工", level=2, page_no=10),
        ParsedBlock(text="5.3.3 混凝土浇筑时的入模温度不宜高于30℃，不宜低于5℃。", page_no=10),
        ParsedBlock(text="5.3.4 混凝土浇筑完毕后应按施工技术方案及时采取有效的养护措施。", page_no=11),
    ]
    chunks = split_blocks(blocks, chunk_size=512, chunk_overlap=0)
    assert chunks, "应产生至少一个分块"
    clause_nos = {c.clause_no for c in chunks}
    assert "5.3.3" in clause_nos
    assert "5.3.4" in clause_nos
    assert all(c.token_count > 0 for c in chunks)


def test_splitter_keeps_long_clause_in_multiple_chunks_with_same_clause_no():
    """超长条款拆多块时必须保留条款号，保证「条款跨块截断率」可控。"""
    long_body = "。".join(f"第{i}款要求应满足相应技术指标" for i in range(200))
    blocks = [ParsedBlock(text=f"7.1.1 {long_body}", page_no=1)]
    chunks = split_blocks(blocks, chunk_size=200, chunk_overlap=0, min_size=50)
    assert len(chunks) > 1
    assert {c.clause_no for c in chunks} == {"7.1.1"}


def test_splitter_overlap_adds_context_within_same_clause():
    body = "。".join(f"第{i}条说明内容应当符合相应规定" for i in range(60))
    blocks = [ParsedBlock(text=f"3.0.1 {body}", page_no=1)]
    chunks = split_blocks(blocks, chunk_size=150, chunk_overlap=30, min_size=40)
    if len(chunks) > 1:
        assert chunks[1].content != ""
        assert chunks[1].token_count > 0


def test_splitter_marks_tables_as_standalone():
    table = "\n".join(f"项目{i} | 允许偏差{i} | 检验方法{i}" for i in range(40))
    blocks = [
        ParsedBlock(text="6.2.1 现浇结构尺寸允许偏差应符合表6.2.1的规定。", page_no=5),
        ParsedBlock(text=table, is_table=True, page_no=5),
    ]
    chunks = split_blocks(blocks, chunk_size=300, chunk_overlap=0)
    assert any(c.is_table for c in chunks)


def test_clean_text_removes_headers_footers_and_watermarks():
    raw = "\n".join(
        [
            "www.example.com 版权所有",
            "第 12 页",
            "5.2.1 水泥进场时应对其品种、级别、出厂日期等进行检查。",
            "- 12 -",
            "   ",
        ]
    )
    cleaned = clean_text(raw)
    assert "水泥进场时应对其品种" in cleaned
    assert "版权所有" not in cleaned
    assert "第 12 页" not in cleaned


def test_clean_text_merges_broken_lines():
    raw = "5.3.3 混凝土浇筑时的入模温度\n不宜高于30℃，不宜低于5℃。"
    cleaned = clean_text(raw)
    assert "不宜高于30℃" in cleaned


# --------------------------------------------------------------------------- #
# BM25（FR-RET-04）
# --------------------------------------------------------------------------- #
def test_tokenize_filters_stopwords_and_short_tokens():
    tokens = tokenize("混凝土的入模温度与养护要求")
    assert "混凝土" in tokens
    assert "养护" in tokens
    assert "的" not in tokens


def test_bm25_ranks_relevant_document_first():
    index = Bm25Index("test")
    index.build(
        [
            Bm25Document(chunk_id=1, text="混凝土浇筑时的入模温度不宜高于30℃"),
            Bm25Document(chunk_id=2, text="脚手架连墙件应从底层第一步纵向水平杆处开始设置"),
            Bm25Document(chunk_id=3, text="钢筋保护层厚度检验应由监理单位见证"),
        ]
    )
    hits = index.search("入模温度", top_k=3)
    assert hits and hits[0].chunk_id == 1
    assert hits[0].rank == 1


def test_bm25_returns_empty_for_unknown_terms():
    index = Bm25Index("test2")
    index.build([Bm25Document(chunk_id=1, text="混凝土养护")])
    assert index.search("量子计算") == []


def test_bm25_incremental_add_and_remove():
    index = Bm25Index("test3")
    index.build([Bm25Document(chunk_id=1, text="混凝土养护时间不得少于7d")])
    index.add([Bm25Document(chunk_id=2, text="抗渗混凝土养护时间不得少于14d")])
    assert index.size == 2
    assert index.get_document(2) is not None
    index.remove([1])
    assert index.size == 1
    assert index.get_document(1) is None


# --------------------------------------------------------------------------- #
# 向量库（SRS 5.3）
# --------------------------------------------------------------------------- #
def test_faiss_index_search_returns_cosine_order():
    embedder = HashEmbedder(dim=128)
    texts = ["混凝土入模温度不宜高于30℃", "脚手架连墙件设置要求", "灌注桩沉渣厚度不应大于50mm"]
    vectors = embedder.encode(texts)
    index = FaissIndex("pytest-vec", dim=128)
    index.add(vectors, [11, 22, 33])

    query = embedder.encode_one("入模温度", is_query=True)
    hits = index.search(query, top_k=3)
    assert len(hits) == 3
    assert hits[0].chunk_id == 11, "哈希向量下词面重叠最多的文档应排第一"
    assert hits[0].score > 0
    assert hits[0].score > hits[1].score, "相似度应按降序排列"
    assert hits[1].score >= hits[2].score
    assert hits[0].rank == 1


def test_faiss_index_remove_rebuilds_without_losing_others():
    embedder = HashEmbedder(dim=64)
    vectors = embedder.encode(["甲条款内容", "乙条款内容", "丙条款内容"])
    index = FaissIndex("pytest-vec2", dim=64)
    index.add(vectors, [1, 2, 3])
    assert index.size == 3
    index.remove([2])
    assert index.size == 2
    hits = index.search(embedder.encode_one("乙条款内容"), top_k=2)
    assert all(hit.chunk_id != 2 for hit in hits)


def test_faiss_index_dimension_mismatch_raises():
    from app.core.errors import VectorSearchError

    index = FaissIndex("pytest-vec3", dim=32)
    index.add([[0.1] * 32], [1])
    with pytest.raises(VectorSearchError):
        index.add([[0.1] * 8], [2])


def test_faiss_search_on_empty_index_returns_empty():
    index = FaissIndex("pytest-vec4", dim=16)
    assert index.search([0.0] * 16, top_k=5) == []


def test_hash_embedder_is_deterministic_and_normalized():
    embedder = HashEmbedder(dim=256)
    first = embedder.encode_one("混凝土入模温度")
    second = embedder.encode_one("混凝土入模温度")
    assert first == second, "哈希向量必须确定性（保证可复现）"
    norm = sum(v * v for v in first) ** 0.5
    assert norm == pytest.approx(1.0, abs=1e-6)


# --------------------------------------------------------------------------- #
# 查询理解（FR-RET-02）
# --------------------------------------------------------------------------- #
def test_understand_query_extracts_intent_entities_and_limits():
    understanding = understand_query("混凝土浇筑入模温度不宜高于多少度？有什么强制要求")
    assert understanding.intent in {"requirement", "clause_lookup", "criterion"}
    assert "混凝土" in " ".join(understanding.entities) or understanding.specialty == "结构工程"
    assert understanding.constraints.get("mandatory") is True or understanding.keywords


def test_understand_query_extracts_clause_candidates():
    understanding = understand_query("按照 GB 50204-2015 第 5.3.3 条的要求")
    assert "5.3.3" in understanding.clause_candidates


def test_understand_query_detects_grade_constraint():
    understanding = understand_query("一级抗震等级框架节点的箍筋要求")
    assert understanding.constraints.get("grade") == "一级"


async def test_multi_query_falls_back_to_templates_without_llm(monkeypatch):
    from app.retrieval import query as query_module

    async def boom(*args, **kwargs):
        raise RuntimeError("llm unavailable")

    monkeypatch.setattr(query_module.get_llm(), "chat_json", boom)
    sub_queries = await query_module.generate_sub_queries("混凝土入模温度要求")
    assert sub_queries[0] == "混凝土入模温度要求", "原问题必须保留在首位"
    assert len(sub_queries) >= 2
    assert len(set(sub_queries)) == len(sub_queries), "子查询应去重"
