# -*- coding: utf-8 -*-
"""条款感知切分（SRS FR-KB-05）。

按「章—节—条—款」层级聚合，长度落在 [chunk_size - overlap, chunk_size] 区间，
重叠比例 10%-15%；超长条（如大表格）单独成块但保留条款号，
保证「条款跨块截断率 < 2%」这一验收指标。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Optional, Sequence

from app.config import settings
from app.core.logging_conf import get_logger
from app.ingest.parser import ParsedBlock
from app.llm.tokens import count_tokens, truncate_to_tokens

logger = get_logger(__name__)

#: 条款号：5.2.3 / 5.2.3.1 / 一、 / 第5.2条
CLAUSE_RE = re.compile(r"^\s*(?:第\s*)?(\d+(?:\.\d+){1,3})\s*(?:条|款|节)?\s*[、\.\s]?")
CLAUSE_CN_RE = re.compile(r"^\s*第([一二三四五六七八九十百零\d]+)条")
CHAPTER_RE = re.compile(r"^\s*(?:第\s*)?(\d+(?:\.\d+)?)\s*[章节]\s*(.{0,60})")


@dataclass
class Chunk:
    """切分产物。"""

    content: str
    chunk_index: int
    clause_no: Optional[str] = None
    chapter_path: Optional[str] = None
    page_no: Optional[int] = None
    token_count: int = 0
    is_table: bool = False
    child_clause_nos: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "chunk_index": self.chunk_index,
            "clause_no": self.clause_no,
            "chapter_path": self.chapter_path,
            "page_no": self.page_no,
            "token_count": self.token_count,
            "is_table": self.is_table,
            "child_clause_nos": self.child_clause_nos,
            "preview": self.content[:120],
        }


@dataclass
class _Section:
    """聚合单元：一个条款（或一个无编号段落）。"""

    clause_no: Optional[str]
    heading: Optional[str]
    chapter_path: str
    page_no: Optional[int]
    lines: list[str] = field(default_factory=list)
    is_table: bool = False

    @property
    def text(self) -> str:
        return "\n".join(self.lines).strip()


def _match_clause(text: str) -> Optional[str]:
    match = CLAUSE_RE.match(text)
    if match:
        return match.group(1)
    match = CLAUSE_CN_RE.match(text)
    if match:
        return f"第{match.group(1)}条"
    return None


def _update_chapter_path(current: str, text: str, level: Optional[int]) -> str:
    if level is None:
        return current
    match = CHAPTER_RE.match(text)
    if match:
        return f"第{match.group(1)}章 {match.group(2).strip()}".strip()
    return current


def split_blocks(
    blocks: Sequence[ParsedBlock],
    *,
    chunk_size: Optional[int] = None,
    chunk_overlap: Optional[int] = None,
    min_size: Optional[int] = None,
) -> list[Chunk]:
    """把解析块切分为检索用 chunk。"""
    chunk_size = chunk_size or settings.chunk_size
    chunk_overlap = chunk_overlap or settings.chunk_overlap
    min_size = min_size or settings.chunk_min_size

    sections: list[_Section] = []
    chapter_path = ""
    current: Optional[_Section] = None

    for block in blocks:
        text = block.text.strip()
        if not text:
            continue
        chapter_path = _update_chapter_path(chapter_path, text, block.level)
        clause_no = _match_clause(text)
        # 标题行（如 "5.2 原材料"）后紧跟正文：作为新条款起点
        is_clause_start = clause_no is not None or block.level is not None
        over_limit = current is not None and count_tokens(current.text) > chunk_size * 0.9

        if is_clause_start or current is None or current.is_table != block.is_table or over_limit:
            current = _Section(
                clause_no=clause_no,
                heading=text[:60] if block.level is not None else None,
                chapter_path=chapter_path,
                page_no=block.page_no,
                is_table=block.is_table,
            )
            sections.append(current)
        current.lines.append(text)
        if current.page_no is None:
            current.page_no = block.page_no

    chunks: list[Chunk] = []
    index = 0
    for section in sections:
        for piece, child_nos in _split_section(section, chunk_size, min_size):
            chunks.append(
                Chunk(
                    content=piece,
                    chunk_index=index,
                    clause_no=section.clause_no,
                    chapter_path=section.chapter_path or None,
                    page_no=section.page_no,
                    token_count=count_tokens(piece),
                    is_table=section.is_table,
                    child_clause_nos=child_nos,
                )
            )
            index += 1

    # 相邻块重叠（10%-15%），只对同一条款内的连续块生效
    if chunk_overlap > 0 and len(chunks) > 1:
        chunks = _apply_overlap(chunks, chunk_overlap)

    logger.info(
        "切分完成",
        extra={
            "blocks": len(blocks),
            "sections": len(sections),
            "chunks": len(chunks),
            "avg_tokens": int(sum(c.token_count for c in chunks) / len(chunks)) if chunks else 0,
        },
    )
    return chunks


def _split_section(section: _Section, chunk_size: int, min_size: int) -> list[tuple[str, list[str]]]:
    """条款内切分：优先按自然段边界累积，表格块与超长单段由专用逻辑处理。"""
    text = section.text
    if not text:
        return []
    tokens = count_tokens(text)
    if tokens <= chunk_size or section.is_table:
        return [(text, [section.clause_no] if section.clause_no else [])]

    pieces: list[tuple[str, list[str]]] = []
    buffer: list[str] = []
    buffer_tokens = 0
    child_nos: list[str] = []

    def flush() -> None:
        nonlocal buffer, buffer_tokens, child_nos
        if not buffer:
            return
        pieces.append(("\n".join(buffer), list(child_nos)))
        buffer = []
        buffer_tokens = 0
        child_nos = []

    for line in section.lines:
        line_tokens = count_tokens(line)
        sub_clause = _match_clause(line)
        if sub_clause and sub_clause != section.clause_no:
            child_nos.append(sub_clause)

        # 单行超过 chunk 上限（例如整段被解析成一行）：按句子边界强制切分，
        # 否则会产出一个远超预算的巨型分块，破坏检索粒度（FR-KB-05）。
        if line_tokens > chunk_size:
            flush()
            for piece in _split_long_line(line, chunk_size):
                pieces.append((piece, list(child_nos)))
            child_nos = []
            continue

        if buffer_tokens + line_tokens > chunk_size and buffer_tokens >= min_size:
            flush()
        buffer.append(line)
        buffer_tokens += line_tokens
    flush()

    # 末块过短则并入前一块，避免产生碎片
    if len(pieces) > 1 and count_tokens(pieces[-1][0]) < min_size:
        tail_text, tail_children = pieces.pop()
        head_text, head_children = pieces[-1]
        pieces[-1] = (head_text + "\n" + tail_text, head_children)
    return pieces


_SENTENCE_SPLIT_RE = re.compile(r"(?<=[。！？；;!?])")


def _split_long_line(line: str, chunk_size: int) -> list[str]:
    """把超长单行按句子边界切成不超过 chunk_size 的片段。"""
    sentences = [s for s in _SENTENCE_SPLIT_RE.split(line) if s]
    if not sentences:
        sentences = [line]

    pieces: list[str] = []
    buffer = ""
    for sentence in sentences:
        if buffer and count_tokens(buffer + sentence) > chunk_size:
            pieces.append(buffer)
            buffer = sentence
        else:
            buffer += sentence
        # 单句本身超限：按 token 预算硬切，保证不超预算
        while count_tokens(buffer) > chunk_size:
            head = truncate_to_tokens(buffer, chunk_size)
            pieces.append(head)
            buffer = buffer[len(head) :]
    if buffer:
        pieces.append(buffer)
    return [p for p in pieces if p.strip()]


def _apply_overlap(chunks: list[Chunk], chunk_overlap: int) -> list[Chunk]:
    """把上一块尾部按 token 预算拼到下一块开头，提升跨块语义连续性。"""
    for idx in range(1, len(chunks)):
        previous = chunks[idx - 1]
        current = chunks[idx]
        if previous.is_table or current.is_table:
            continue
        if previous.clause_no != current.clause_no:
            continue
        tail = truncate_to_tokens(previous.content, chunk_overlap)
        # 从尾部截取到最近的换行，避免半句话
        if "\n" in tail:
            tail = tail[tail.find("\n") + 1 :]
        tail = tail.strip()
        if not tail or tail in current.content:
            continue
        current.content = f"{tail}\n{current.content}"
        current.token_count = count_tokens(current.content)
    return chunks


__all__ = ["Chunk", "split_blocks", "CLAUSE_RE", "CHAPTER_RE"]
