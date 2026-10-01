# -*- coding: utf-8 -*-
"""检索领域数据结构。"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional


@dataclass
class Citation:
    """引用溯源（SRS FR-RET-09）：必须能定位到文件、章节、条款号、页码。"""

    doc_id: int
    version_id: Optional[int]
    spec_code: Optional[str]
    spec_name: Optional[str]
    clause_no: Optional[str]
    chapter_path: Optional[str]
    page_no: Optional[int]
    chunk_id: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "doc_id": self.doc_id,
            "version_id": self.version_id,
            "spec_code": self.spec_code,
            "spec_name": self.spec_name,
            "clause_no": self.clause_no,
            "chapter_path": self.chapter_path,
            "page_no": self.page_no,
            "chunk_id": self.chunk_id,
        }

    def label(self) -> str:
        parts = [p for p in (self.spec_code, self.clause_no) if p]
        return " ".join(parts) if parts else f"chunk#{self.chunk_id}"

    def render(self) -> str:
        """人类可读出处，用于报告与答案。"""
        spec = " ".join(p for p in (self.spec_code, self.spec_name) if p)
        tail = []
        if self.chapter_path:
            tail.append(f"章节：{self.chapter_path}")
        if self.clause_no:
            tail.append(f"条款：{self.clause_no}")
        if self.page_no:
            tail.append(f"页码：P{self.page_no}")
        return f"{spec}（{'；'.join(tail)}）" if tail else spec


@dataclass
class ChunkRecord:
    """参与检索的文本块（来自 PostgreSQL doc_chunk + spec_doc 元数据）。"""

    chunk_id: int
    content: str
    doc_id: int
    version_id: Optional[int] = None
    clause_no: Optional[str] = None
    chapter_path: Optional[str] = None
    page_no: Optional[int] = None
    spec_code: Optional[str] = None
    spec_name: Optional[str] = None
    token_count: Optional[int] = None

    def citation(self) -> Citation:
        return Citation(
            doc_id=self.doc_id,
            version_id=self.version_id,
            spec_code=self.spec_code,
            spec_name=self.spec_name,
            clause_no=self.clause_no,
            chapter_path=self.chapter_path,
            page_no=self.page_no,
            chunk_id=self.chunk_id,
        )


@dataclass
class RetrievedClause:
    chunk_id: int
    content: str
    citation: Citation
    rrf_score: float = 0.0
    relevance_score: float = 0.0
    sources: list[str] = field(default_factory=list)
    best_rank: int = 10**6
    kg_related: list[dict[str, Any]] = field(default_factory=list)
    degraded: bool = False
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "chunk_id": self.chunk_id,
            "clause_no": self.citation.clause_no,
            "spec_code": self.citation.spec_code,
            "spec_name": self.citation.spec_name,
            "chapter_path": self.citation.chapter_path,
            "page_no": self.citation.page_no,
            "content": self.content,
            "rrf_score": round(self.rrf_score, 6),
            "relevance_score": round(self.relevance_score, 6),
            "source": self.sources,
            "kg_related": self.kg_related,
            "citation": self.citation.to_dict(),
        }


@dataclass
class QueryUnderstanding:
    """查询理解结果（SRS FR-RET-02）。"""

    original: str
    intent: str = "clause_lookup"
    entities: list[str] = field(default_factory=list)
    specialty: Optional[str] = None
    constraints: dict[str, Any] = field(default_factory=dict)
    keywords: list[str] = field(default_factory=list)
    clause_candidates: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "original": self.original,
            "intent": self.intent,
            "entities": self.entities,
            "specialty": self.specialty,
            "constraints": self.constraints,
            "keywords": self.keywords,
            "clause_candidates": self.clause_candidates,
        }


@dataclass
class RetrievalResult:
    query: str
    understanding: QueryUnderstanding
    sub_queries: list[str] = field(default_factory=list)
    clauses: list[RetrievedClause] = field(default_factory=list)
    no_evidence: bool = False
    latency_ms: int = 0
    degraded: bool = False
    debug: dict[str, Any] = field(default_factory=dict)

    def citations(self) -> list[Citation]:
        return [c.citation for c in self.clauses]

    def to_dict(self, include_content: bool = True) -> dict[str, Any]:
        items = []
        for clause in self.clauses:
            data = clause.to_dict()
            if not include_content:
                data.pop("content", None)
            items.append(data)
        return {
            "query": self.query,
            "understanding": self.understanding.to_dict(),
            "sub_queries": self.sub_queries,
            "results": items,
            "no_evidence": self.no_evidence,
            "latency_ms": self.latency_ms,
            "degraded": self.degraded,
            # 检索链路指标（双通道召回量、RRF、图谱扩展、模型名），前端「检索链路」面板依赖
            "debug": self.debug,
        }


__all__ = [
    "Citation",
    "ChunkRecord",
    "RetrievedClause",
    "QueryUnderstanding",
    "RetrievalResult",
]
