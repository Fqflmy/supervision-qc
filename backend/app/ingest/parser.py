# -*- coding: utf-8 -*-
"""文档解析（SRS FR-KB-03/04）。

支持 PDF（pdfplumber，按版面提取标题/段落/表格与页码）、Word（python-docx，保留标题层级）、
TXT、Excel。解析后统一清洗：去页眉页脚、去水印、合并断行、修正全半角。
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from app.config import settings
from app.core.errors import AppError
from app.core.logging_conf import get_logger

logger = get_logger(__name__)


class ParseError(AppError):
    code_key = "PARAM_INVALID"
    http_status = 400
    message = "文档解析失败"


@dataclass
class ParsedBlock:
    """解析出的结构化块。"""

    text: str
    page_no: Optional[int] = None
    level: Optional[int] = None  # 标题层级，1 为一级标题
    is_table: bool = False
    order: int = 0


@dataclass
class ParsedDocument:
    blocks: list[ParsedBlock] = field(default_factory=list)
    page_count: int = 0
    file_hash: str = ""
    file_type: str = ""
    meta: dict[str, Any] = field(default_factory=dict)

    @property
    def full_text(self) -> str:
        return "\n".join(b.text for b in self.blocks)

    @property
    def char_count(self) -> int:
        return sum(len(b.text) for b in self.blocks)


# --------------------------------------------------------------------------- #
# 清洗规则（FR-KB-04）
# --------------------------------------------------------------------------- #
_HEADER_FOOTER_RE = re.compile(
    r"^\s*(?:第\s*\d+\s*页|共\s*\d+\s*页|Page\s*\d+(?:\s*of\s*\d+)?|[-—]\s*\d+\s*[-—])\s*$",
    re.I,
)
_WATERMARK_RE = re.compile(r"(?:版权所有|仅供内部使用|严禁外传|水印|下载于|http[s]?://\S+)")
_PAGE_NO_ONLY_RE = re.compile(r"^\s*[-—·.]*\s*\d{1,4}\s*[-—·.]*\s*$")
_FULLWIDTH_MAP = {
    "（": "（", "）": "）", "，": "，", "：": "：", "；": "；",
    "０": "0", "１": "1", "２": "2", "３": "3", "４": "4",
    "５": "5", "６": "6", "７": "7", "８": "8", "９": "9",
}
_CLAUSE_HEAD_RE = re.compile(r"^\s*(\d+(?:\.\d+){0,3})\s*[、\.]?\s*(.{0,60})")


def clean_text(text: str) -> str:
    """单块文本清洗。"""
    if not text:
        return ""
    lines: list[str] = []
    for raw in text.splitlines():
        line = raw.replace("\u3000", " ").rstrip()
        for src, dst in _FULLWIDTH_MAP.items():
            line = line.replace(src, dst)
        line = re.sub(r"[ \t]{2,}", " ", line)
        if not line.strip():
            continue
        if _PAGE_NO_ONLY_RE.match(line) or _HEADER_FOOTER_RE.match(line):
            continue
        line = _WATERMARK_RE.sub("", line).strip()
        if not line:
            continue
        lines.append(line)
    merged = _merge_broken_lines(lines)
    return merged.strip()


def _merge_broken_lines(lines: Sequence[str]) -> str:
    """合并被 PDF 硬换行切断的句子：上一行未以句末标点结尾且下一行非条款开头则拼接。"""
    if not lines:
        return ""
    out: list[str] = []
    for line in lines:
        if not out:
            out.append(line)
            continue
        previous = out[-1]
        starts_new_clause = bool(_CLAUSE_HEAD_RE.match(line)) and len(line) < 80
        previous_ends = previous.endswith(("。", "；", "：", "！", "？", "）", ")", "：", "”", '"'))
        if not previous_ends and not starts_new_clause and len(previous) > 8:
            out[-1] = previous + line
        else:
            out.append(line)
    return "\n".join(out)


def file_sha1(path: Path) -> str:
    digest = hashlib.sha1()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --------------------------------------------------------------------------- #
# 各格式解析
# --------------------------------------------------------------------------- #
def parse_pdf(path: Path) -> ParsedDocument:
    import pdfplumber

    blocks: list[ParsedBlock] = []
    order = 0
    with pdfplumber.open(str(path)) as pdf:
        page_count = len(pdf.pages)
        for page_index, page in enumerate(pdf.pages, start=1):
            try:
                tables = page.extract_tables() or []
            except Exception:  # noqa: BLE001
                tables = []
            table_texts = {_table_to_text(t) for t in tables if t}
            text = page.extract_text() or ""
            for raw_line in text.splitlines():
                line = clean_text(raw_line)
                if not line:
                    continue
                if line in table_texts:
                    continue
                level = _heading_level(line)
                blocks.append(
                    ParsedBlock(text=line, page_no=page_index, level=level, order=order)
                )
                order += 1
            for table in tables:
                if not table:
                    continue
                rendered = _table_to_text(table)
                if rendered:
                    blocks.append(
                        ParsedBlock(
                            text=rendered, page_no=page_index, is_table=True, order=order
                        )
                    )
                    order += 1

    char_count = sum(len(b.text) for b in blocks)
    min_expected = max(1, page_count) * settings.parser_min_text_chars_per_page
    if char_count < min_expected:
        logger.warning(
            "PDF 可提取文本过少，可能是扫描件",
            extra={"path": str(path), "page_count": page_count, "chars": char_count, "ocr_enabled": settings.parser_ocr_enabled},
        )
        if settings.parser_ocr_enabled:
            blocks.extend(_ocr_fallback(path))
    return ParsedDocument(
        blocks=blocks,
        page_count=page_count,
        file_type="pdf",
        meta={"char_count": char_count, "table_count": sum(1 for b in blocks if b.is_table)},
    )


def _table_to_text(table: Sequence[Sequence[Any]]) -> str:
    rows: list[str] = []
    for row in table:
        cells = [str(c).strip().replace("\n", " ") for c in row if c is not None and str(c).strip()]
        if cells:
            rows.append(" | ".join(cells))
    return "\n".join(rows)


def _ocr_fallback(path: Path) -> list[ParsedBlock]:
    """OCR 降级链路（需安装 paddleocr / pytesseract；未安装时安全跳过）。"""
    try:
        from pdf2image import convert_from_path  # type: ignore

        images = convert_from_path(str(path), dpi=200)
    except Exception as exc:  # noqa: BLE001
        logger.warning("OCR 预处理失败，跳过", extra={"error": str(exc)[:200]})
        return []
    blocks: list[ParsedBlock] = []
    try:
        import pytesseract  # type: ignore

        for page_index, image in enumerate(images, start=1):
            text = pytesseract.image_to_string(image, lang="chi_sim+eng")
            cleaned = clean_text(text)
            if cleaned:
                blocks.append(ParsedBlock(text=cleaned, page_no=page_index))
    except Exception as exc:  # noqa: BLE001
        logger.warning("OCR 引擎不可用，跳过", extra={"error": str(exc)[:200]})
    return blocks


def parse_docx(path: Path) -> ParsedDocument:
    from docx import Document

    document = Document(str(path))
    blocks: list[ParsedBlock] = []
    order = 0
    for paragraph in document.paragraphs:
        text = clean_text(paragraph.text)
        if not text:
            continue
        style = (paragraph.style.name or "").lower() if paragraph.style is not None else ""
        level = None
        if style.startswith("heading"):
            digits = re.findall(r"\d+", style)
            level = int(digits[0]) if digits else 1
        else:
            level = _heading_level(text)
        blocks.append(ParsedBlock(text=text, level=level, order=order))
        order += 1
    for table in document.tables:
        rows = []
        for row in table.rows:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells if cell.text.strip()]
            if cells:
                rows.append(" | ".join(cells))
        rendered = "\n".join(rows)
        if rendered:
            blocks.append(ParsedBlock(text=rendered, is_table=True, order=order))
            order += 1
    return ParsedDocument(
        blocks=blocks,
        page_count=0,
        file_type="docx",
        meta={"paragraphs": len(document.paragraphs), "tables": len(document.tables)},
    )


def parse_doc(path: Path) -> ParsedDocument:
    """旧版 .doc：优先用 LibreOffice 转换，否则按二进制文本粗提取。"""
    try:
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            result = subprocess.run(
                ["soffice", "--headless", "--convert-to", "docx", "--outdir", tmp, str(path)],
                capture_output=True,
                timeout=120,
            )
            converted = Path(tmp) / (path.stem + ".docx")
            if result.returncode == 0 and converted.exists():
                parsed = parse_docx(converted)
                parsed.file_type = "doc"
                return parsed
    except Exception as exc:  # noqa: BLE001
        logger.warning("LibreOffice 转换 .doc 失败，尝试二进制提取", extra={"error": str(exc)[:200]})
    raw = path.read_bytes()
    text = raw.decode("gb18030", errors="ignore")
    blocks = [ParsedBlock(text=clean_text(text), order=0)]
    return ParsedDocument(blocks=blocks, file_type="doc", meta={"fallback": "binary"})


def parse_txt(path: Path) -> ParsedDocument:
    raw = path.read_bytes()
    for encoding in ("utf-8", "gb18030", "utf-16"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:  # pragma: no cover
        text = raw.decode("utf-8", errors="ignore")
    blocks: list[ParsedBlock] = []
    order = 0
    for line in text.splitlines():
        cleaned = clean_text(line)
        if not cleaned:
            continue
        blocks.append(ParsedBlock(text=cleaned, level=_heading_level(cleaned), order=order))
        order += 1
    return ParsedDocument(blocks=blocks, file_type="txt")


def parse_xlsx(path: Path) -> ParsedDocument:
    from openpyxl import load_workbook

    workbook = load_workbook(str(path), data_only=True, read_only=True)
    blocks: list[ParsedBlock] = []
    order = 0
    for sheet in workbook.worksheets:
        rows: list[str] = []
        for row in sheet.iter_rows(values_only=True):
            cells = [str(c).strip() for c in row if c is not None and str(c).strip()]
            if cells:
                rows.append(" | ".join(cells))
        if rows:
            blocks.append(
                ParsedBlock(text=f"【{sheet.title}】\n" + "\n".join(rows), is_table=True, order=order)
            )
            order += 1
    workbook.close()
    return ParsedDocument(blocks=blocks, file_type="xlsx", meta={"sheets": len(workbook.sheetnames)})


_HEADING_PATTERNS = (
    re.compile(r"^第[一二三四五六七八九十百零\d]+[章节部分]"),
    re.compile(r"^\d+(?:\.\d+){0,2}\s*[、\s]\s*\S"),
    re.compile(r"^[一二三四五六七八九十]+[、\.]\s*\S"),
    re.compile(r"^附[录件]\s*[A-Za-z\d]?"),
)


def _heading_level(text: str) -> Optional[int]:
    for index, pattern in enumerate(_HEADING_PATTERNS, start=1):
        if pattern.match(text) and len(text) <= 60:
            return min(index, 3)
    return None


_PARSERS = {
    ".pdf": parse_pdf,
    ".docx": parse_docx,
    ".doc": parse_doc,
    ".txt": parse_txt,
    ".xlsx": parse_xlsx,
    ".xls": parse_xlsx,
}


def parse_document(path: str | Path) -> ParsedDocument:
    """按扩展名分发解析，并完成哈希与基础校验。"""
    target = Path(path)
    if not target.exists():
        raise ParseError(f"文件不存在：{target}")
    suffix = target.suffix.lower()
    parser = _PARSERS.get(suffix)
    if parser is None:
        raise ParseError(f"不支持的文档类型：{suffix}")
    logger.info("开始解析文档", extra={"file": target.name, "type": suffix, "size": target.stat().st_size})
    parsed = parser(target)
    parsed.file_hash = file_sha1(target)
    parsed.blocks = [b for b in parsed.blocks if b.text.strip()]
    if not parsed.blocks:
        raise ParseError(f"未从文档中提取到任何文本：{target.name}")
    logger.info(
        "文档解析完成",
        extra={
            "file": target.name,
            "blocks": len(parsed.blocks),
            "chars": parsed.char_count,
            "pages": parsed.page_count,
        },
    )
    return parsed


__all__ = [
    "ParsedBlock",
    "ParsedDocument",
    "parse_document",
    "clean_text",
    "file_sha1",
    "ParseError",
]
