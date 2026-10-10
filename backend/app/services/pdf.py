# -*- coding: utf-8 -*-
"""评估报告 PDF 导出（FR-AGT-08 报告生成的可交付形态）。

为什么用 reportlab + 内置 CID 中文字体
--------------------------------------
运行镜像是 ``python:3.12-slim``：**没有 CJK 字体文件，也没有 apt 系统包**
（Dockerfile 明确说明「不安装 apt 系统包，受限网络下更可靠」）。

因此排除了两条常见路线：
- **WeasyPrint**：需要 libpango/libcairo/gdk-pixbuf 等系统库 + 字体文件，
  与该镜像的设计前提冲突；
- **打包 TTF 字体 + 子集化**：需要把几 MB 字体文件提交进仓库，
  且字体授权需逐个确认。

reportlab 的 ``UnicodeCIDFont('STSong-Light')`` 是 **Adobe CJK CID 字体**：
PDF 里只写字符编码、字形由阅读器提供，**无需字体文件**，
在 Windows/macOS/Linux 的主流阅读器与浏览器中都能正常显示中文。

排版
----
用 Platypus（reportlab 的高层排版引擎）流式排版，支持：
标题层级、章节编号、表格（自动换行 + 列宽自适应）、
有序/无序列表、粗体与行内代码、页眉页脚与页码。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Iterable, Sequence

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    KeepTogether,
    PageTemplate,
    Paragraph,
    Spacer,
    Table,
    TableStyle,
)

#: CID 中文字体名。register 一次即可全局使用。
CJK_FONT = "STSong-Light"
CJK_FONT_BOLD = "STSong-Light"  # CID 字体无独立粗体，用字号/颜色区分层级

#: 是否需要检测「字体是否已注册」（registerFont 重复调用会抛异常）
_registered = False


def _ensure_font() -> None:
    """注册 CID 中文字体（幂等）。"""
    global _registered
    if not _registered:
        pdfmetrics.registerFont(UnicodeCIDFont(CJK_FONT))
        _registered = True


# --------------------------------------------------------------------------- #
# 样式
# --------------------------------------------------------------------------- #
def _styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    body = ParagraphStyle(
        "CJKBody",
        parent=base["BodyText"],
        fontName=CJK_FONT,
        fontSize=9.5,
        leading=16,
        alignment=TA_LEFT,
        textColor=colors.HexColor("#1f2937"),
    )
    return {
        "title": ParagraphStyle(
            "CJKTitle",
            parent=body,
            fontSize=17,
            leading=26,
            spaceAfter=2,
            textColor=colors.HexColor("#0f172a"),
        ),
        "h2": ParagraphStyle(
            "CJKH2",
            parent=body,
            fontSize=12.5,
            leading=21,
            spaceBefore=11,
            spaceAfter=5,
            textColor=colors.HexColor("#0f172a"),
        ),
        "h3": ParagraphStyle(
            "CJKH3",
            parent=body,
            fontSize=10.5,
            leading=18,
            spaceBefore=8,
            spaceAfter=4,
            textColor=colors.HexColor("#1f2937"),
        ),
        "body": body,
        "cell": ParagraphStyle(
            "CJKCell",
            parent=body,
            fontSize=8.2,
            leading=12.5,
            spaceBefore=0,
            spaceAfter=0,
        ),
        "cell_head": ParagraphStyle(
            "CJKCellHead",
            parent=body,
            fontSize=8.4,
            leading=12.5,
            textColor=colors.HexColor("#0f172a"),
            spaceBefore=0,
            spaceAfter=0,
        ),
        "bullet": ParagraphStyle(
            "CJKBullet",
            parent=body,
            leftIndent=10,
            bulletIndent=2,
            spaceBefore=1,
            spaceAfter=1,
        ),
        "meta": ParagraphStyle(
            "CJKMeta",
            parent=body,
            fontSize=8.5,
            leading=14,
            textColor=colors.HexColor("#64748b"),
        ),
        "note": ParagraphStyle(
            "CJKNote",
            parent=body,
            fontSize=8.5,
            leading=14,
            textColor=colors.HexColor("#92400e"),
        ),
    }


# --------------------------------------------------------------------------- #
# 行内标记 → reportlab 标签
# --------------------------------------------------------------------------- #
def _inline(text: str) -> str:
    """把 Markdown 行内标记转成 reportlab 支持的标签。

    reportlab 的 Paragraph 只认有限标签（``<b> <i> <font> <br/>`` 等），
    因此需要转换而不是直接塞 Markdown。

    ⚠️ 必须先做 XML 转义，再插入标签；否则原文里的 ``<`` ``&``
    会被当成标签导致整段解析失败（报告正文常含 ``≤``、``GB/T 50107`` 等，
    但也可能含 ``<0.5mm`` 这类比较符）。
    """
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    # 行内代码：用等宽感（CID 无等宽字体，用颜色区分）
    text = re.sub(
        r"`([^`]+)`",
        r'<font color="#b45309">\1</font>',
        text,
    )
    # 粗体
    text = re.sub(r"\*\*([^*]+)\*\*", r"<b>\1</b>", text)
    # 斜体（单星号，注意不要吃掉已处理的粗体）
    text = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", text)
    # 链接 [文字](url) → 文字（url）
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1（\2）", text)
    return text


def _is_table_sep(line: str) -> bool:
    """判断是否表格分隔行（| --- | --- |）。"""
    return bool(re.fullmatch(r"\|[\s:|-]+\|", line.strip()))


def _split_row(line: str) -> list[str]:
    """拆分表格行；去掉首尾空管道。"""
    raw = line.strip()
    if raw.startswith("|"):
        raw = raw[1:]
    if raw.endswith("|"):
        raw = raw[:-1]
    return [c.strip() for c in raw.split("|")]


# --------------------------------------------------------------------------- #
# 列宽估算
# --------------------------------------------------------------------------- #
def _column_widths(rows: Sequence[Sequence[str]], avail: float) -> list[float]:
    """按各列内容长度估算列宽占比。

    为什么不用等宽：报告表格列数 2~6 不等，内容长度差异极大
    （「序号」只有 1~2 字，「判定理由」可能有 200 字）。
    等宽会让长文本列挤成极窄的一条、反复折行难以阅读。
    """
    ncols = max(len(r) for r in rows)
    # 用前若干行（含表头）估算，避免被超长单元格完全主导
    weights = [0.0] * ncols
    for row in rows[:12]:
        for i, cell in enumerate(row):
            # 中文按 2 个宽度计，并设下限避免空列权重为 0
            length = sum(2 if ord(ch) > 0x2E80 else 1 for ch in str(cell))
            weights[i] += min(length, 120)
    total = sum(weights) or 1.0
    # 给每列一个最小占比，避免出现细如发丝的列
    min_ratio = 0.06
    ratios = [max(w / total, min_ratio) for w in weights]
    scale = sum(ratios)
    return [avail * r / scale for r in ratios]


# --------------------------------------------------------------------------- #
# Markdown → Platypus flowables
# --------------------------------------------------------------------------- #
def markdown_to_flowables(markdown: str, avail_width: float) -> list[Any]:
    """把报告 Markdown 转成 reportlab 流式元素。

    支持的语法（覆盖本项目报告模板实际用到的全部语法）：
    ``#``~``####`` 标题、``|`` 表格、``-``/``*`` 无序列表、
    ``1.`` 有序列表（含两空格缩进的嵌套）、``**粗体**``、行内代码、空行分段。
    """
    _ensure_font()
    st = _styles()
    flow: list[Any] = []
    lines = markdown.splitlines()
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()

        # ---- 空行 ----
        if not stripped:
            i += 1
            continue

        # ---- 标题 ----
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = len(m.group(1))
            text = _inline(m.group(2))
            if level == 1:
                flow.append(Paragraph(text, st["title"]))
                flow.append(Spacer(1, 3 * mm))
            elif level == 2:
                flow.append(Paragraph(text, st["h2"]))
            else:
                flow.append(Paragraph(text, st["h3"]))
            i += 1
            continue

        # ---- 表格 ----
        if stripped.startswith("|") and i + 1 < len(lines) and _is_table_sep(lines[i + 1]):
            header = _split_row(stripped)
            body_rows: list[list[str]] = []
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                body_rows.append(_split_row(lines[i]))
                i += 1

            all_rows = [header] + body_rows
            widths = _column_widths(all_rows, avail_width)
            data = []
            for r_idx, row in enumerate(all_rows):
                cells = []
                for c_idx in range(len(widths)):
                    raw = row[c_idx] if c_idx < len(row) else ""
                    style = st["cell_head"] if r_idx == 0 else st["cell"]
                    cells.append(Paragraph(_inline(raw) or "&nbsp;", style))
                data.append(cells)

            table = Table(data, colWidths=widths, repeatRows=1, hAlign="LEFT")
            table.setStyle(
                TableStyle(
                    [
                        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#eef2f7")),
                        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c7d2e0")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 4),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
                        ("TOPPADDING", (0, 0), (-1, -1), 3),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                        # 斑马纹提升长表可读性
                        *[
                            ("BACKGROUND", (0, r), (-1, r), colors.HexColor("#f8fafc"))
                            for r in range(2, len(data), 2)
                        ],
                    ]
                )
            )
            flow.append(Spacer(1, 1.5 * mm))
            flow.append(table)
            flow.append(Spacer(1, 2.5 * mm))
            continue

        # ---- 列表（含嵌套）----
        m = re.match(r"^(\s*)([-*+]|\d+\.)\s+(.*)$", line)
        if m:
            indent = len(m.group(1))
            marker = m.group(2)
            text = _inline(m.group(3))
            style = ParagraphStyle(
                f"CJKList{indent}",
                parent=st["bullet"],
                leftIndent=10 + indent * 6,
                bulletIndent=0 + indent * 6,
            )
            if marker.endswith("."):
                bullet = marker
            else:
                bullet = "•"
            flow.append(Paragraph(text, style, bulletText=bullet))
            i += 1
            continue

        # ---- 分隔线 ----
        if re.fullmatch(r"[-*_]{3,}", stripped):
            flow.append(Spacer(1, 2 * mm))
            i += 1
            continue

        # ---- 普通段落（合并连续行）----
        para: list[str] = [stripped]
        i += 1
        while (
            i < len(lines)
            and lines[i].strip()
            and not re.match(r"^(#{1,6})\s", lines[i])
            and not lines[i].strip().startswith("|")
            and not re.match(r"^(\s*)([-*+]|\d+\.)\s+", lines[i])
            and not re.fullmatch(r"[-*_]{3,}", lines[i].strip())
        ):
            para.append(lines[i].strip())
            i += 1
        flow.append(Paragraph(_inline(" ".join(para)), st["body"]))
        flow.append(Spacer(1, 1.5 * mm))

    return flow


# --------------------------------------------------------------------------- #
# 文档级排版（页眉页脚 + 元信息头）
# --------------------------------------------------------------------------- #
def build_report_pdf(
    *,
    title: str,
    markdown: str,
    meta: dict[str, Any],
    output: Any,
) -> None:
    """生成正式版评估报告 PDF。

    ``meta`` 用于页眉与首页信息块，建议包含：
    ``report_no`` / ``specialty`` / ``project`` / ``verdict`` / ``risk`` /
    ``review_decision`` / ``review_status`` / ``reviewed_by`` / ``finished_at``。

    ⚠️ **签发状态必须印在报告上**：未签发的报告不得作为正式依据，
    纸质/PDF 一旦流出就脱离了系统界面，若文件本身不写签发状态，
    接收方无从判断这份报告是否已被人工复核。
    """
    _ensure_font()
    st = _styles()
    pw, ph = A4
    margin_x = 18 * mm
    avail = pw - 2 * margin_x

    doc = BaseDocTemplate(
        output,
        pagesize=A4,
        leftMargin=margin_x,
        rightMargin=margin_x,
        topMargin=20 * mm,
        bottomMargin=16 * mm,
        title=title,
        author=meta.get("reviewed_by") or "工程监理质量智能评估系统",
    )

    header_text = meta.get("footer_left") or title

    def _page(canvas, _doc) -> None:
        """页眉页脚装饰。

        ⚠️ reportlab 的 onPage 回调签名是 ``(canvas, doc)``，
        页码需从 ``doc.page`` 取 —— 不是 ``(canvas, doc, page_no)``。
        """
        page_no = getattr(_doc, "page", 1)
        canvas.saveState()
        # 页眉：报告名 + 签发状态（每页都有，避免抽页后无法判断）
        canvas.setFont(CJK_FONT, 7.5)
        canvas.setFillColor(colors.HexColor("#94a3b8"))
        canvas.drawString(margin_x, ph - 12 * mm, header_text[:70])
        status = meta.get("review_status") or ""
        if status:
            canvas.drawRightString(pw - margin_x, ph - 12 * mm, f"报告状态：{status}")
        canvas.setStrokeColor(colors.HexColor("#e2e8f0"))
        canvas.setLineWidth(0.5)
        canvas.line(margin_x, ph - 14 * mm, pw - margin_x, ph - 14 * mm)
        # 页脚：页码 + 生成时间
        canvas.line(margin_x, 13 * mm, pw - margin_x, 13 * mm)
        canvas.setFont(CJK_FONT, 7.5)
        canvas.drawString(margin_x, 9.5 * mm, f"生成时间：{datetime.now():%Y-%m-%d %H:%M}")
        canvas.drawRightString(pw - margin_x, 9.5 * mm, f"第 {page_no} 页")
        canvas.restoreState()

    doc.addPageTemplates(
        [
            PageTemplate(
                id="main",
                frames=[Frame(margin_x, 16 * mm, avail, ph - 36 * mm)],
                onPage=_page,
            )
        ]
    )

    flow: list[Any] = []

    # ---- 首页信息块 ----
    flow.append(Paragraph(_inline(title), st["title"]))
    flow.append(Spacer(1, 2 * mm))

    meta_rows: list[list[str]] = []
    for label, key in (
        ("报告编号", "report_no"),
        ("评估专业", "specialty"),
        ("所属项目", "project"),
        ("评估结论", "verdict"),
        ("风险等级", "risk"),
        ("复核决定", "review_decision"),
        ("签发状态", "review_status"),
        ("复核人", "reviewed_by"),
        ("完成时间", "finished_at"),
    ):
        value = meta.get(key)
        if value:
            meta_rows.append([label, str(value)])

    if meta_rows:
        data = [
            [
                Paragraph(_inline(a), st["cell_head"]),
                Paragraph(_inline(b), st["cell"]),
            ]
            for a, b in meta_rows
        ]
        t = Table(data, colWidths=[avail * 0.18, avail * 0.82], hAlign="LEFT")
        t.setStyle(
            TableStyle(
                [
                    ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#c7d2e0")),
                    ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#eef2f7")),
                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
                    ("LEFTPADDING", (0, 0), (-1, -1), 4),
                    ("TOPPADDING", (0, 0), (-1, -1), 3),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
                ]
            )
        )
        flow.append(t)
        flow.append(Spacer(1, 2 * mm))

    # ---- 未签发警示（印在文件上）----
    if not meta.get("is_final", False):
        flow.append(
            Paragraph(
                "⚠ 本报告尚未签发：仅供内部参考，<b>不得作为对外出具的正式依据</b>。"
                "签发后方可作为正式文件使用。",
                st["note"],
            )
        )
        flow.append(Spacer(1, 3 * mm))

    # ---- 正文 ----
    body_flow = markdown_to_flowables(markdown, avail)
    flow.extend(body_flow)

    doc.build(flow)


# --------------------------------------------------------------------------- #
# 便捷入口
# --------------------------------------------------------------------------- #
def safe_filename(text: str, fallback: str = "评估报告") -> str:
    """把标题转成安全的下载文件名（去掉路径分隔符与控制字符）。"""
    cleaned = re.sub(r'[\\/:*?"<>|\r\n\t]+', "_", text or "").strip(" .")
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    return (cleaned or fallback)[:80]


def ascii_filename(text: str, fallback: str = "report") -> str:
    """生成 ASCII 回退文件名。

    ⚠️ **HTTP 头只能用 latin-1 编码**：把中文直接放进 ``filename="..."``
    会让 Starlette 在构造响应头时抛
    ``UnicodeEncodeError: 'latin-1' codec can't encode characters``，
    接口直接 500（实际踩到）。

    RFC 6266/5987 的规范做法是**两个都给**：
    - ``filename="..."``：ASCII 回退，给不支持 ``filename*`` 的老客户端；
    - ``filename*=UTF-8''...``：UTF-8 编码的真名，现代浏览器优先使用。

    因此这里把非 ASCII 字符全部剥掉；若结果为空则用 ``fallback``
    （例如纯中文标题会退化成 ``report.pdf``，但浏览器实际会用 ``filename*`` 里的中文名）。
    """
    cleaned = re.sub(r"[^\x20-\x7e]+", "", text or "")
    cleaned = re.sub(r'[\\/:*?"<>|]+', "_", cleaned).strip(" ._-")
    cleaned = re.sub(r"_{2,}", "_", cleaned)
    return cleaned[:80] or fallback
