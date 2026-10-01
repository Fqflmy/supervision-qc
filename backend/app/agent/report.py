# -*- coding: utf-8 -*-
"""报告渲染（SRS FR-AGT-07 / 7.6 合规声明）。

渲染成结构化 Markdown，便于前端展示与导出 PDF/Word。
"""
from __future__ import annotations

import re
from typing import Any, Optional, Sequence

from app.constants import VERDICT_LABELS, Verdict

#: 渲染器自带的标准章节关键词，用于过滤模型重复给出的同名章节
_STANDARD_SECTION_KEYWORDS = (
    "评估概述",
    "评估依据",
    "逐条比对",
    "条款匹配",
    "问题清单",
    "整改建议",
    "总体结论",
    "引用条款溯源",
)

DISCLAIMER = (
    "> 本报告由工程监理质量智能评估系统辅助生成，结论基于系统检索到的规范条款与提交的待评材料自动推导。"
    "系统输出不替代监理工程师的专业判断与签字责任，最终结论以监理工程师签字确认为准。"
)

VERDICT_RANK = {
    Verdict.NON_COMPLIANT.value: 0,
    Verdict.INSUFFICIENT_EVIDENCE.value: 1,
    Verdict.PARTIAL.value: 2,
    Verdict.COMPLIANT.value: 3,
    Verdict.NOT_APPLICABLE.value: 4,
}


def verdict_label(verdict: Optional[str]) -> str:
    if not verdict:
        return "未判定"
    try:
        return VERDICT_LABELS[Verdict(verdict)]
    except (ValueError, KeyError):
        return str(verdict)


def render_markdown(
    report: dict[str, Any],
    *,
    task_meta: Optional[dict[str, Any]] = None,
    matches: Optional[Sequence[dict[str, Any]]] = None,
    evidence_basis: Optional[Sequence[dict[str, Any]]] = None,
) -> str:
    """把报告 JSON 渲染为 Markdown。"""
    meta = task_meta or {}
    matches = list(matches or [])
    evidence_basis = list(evidence_basis or [])

    lines: list[str] = []
    title = report.get("title") or "工程质量评估报告"
    lines.append(f"# {title}")
    lines.append("")

    # 一、评估概述
    lines.append("## 一、评估概述")
    lines.append("")
    overview_rows = [
        ("项目名称", meta.get("project_name") or "-"),
        ("评估专业", meta.get("specialty") or "-"),
        ("评估对象", meta.get("part") or "-"),
        ("评估类型", meta.get("eval_type") or "inspection_lot"),
        ("评估时间", meta.get("eval_time") or "-"),
        ("任务编号", meta.get("task_id") or "-"),
    ]
    lines.append("| 项目 | 内容 |")
    lines.append("| --- | --- |")
    for key, value in overview_rows:
        lines.append(f"| {key} | {value} |")
    lines.append("")
    if report.get("overview"):
        lines.append(str(report["overview"]))
        lines.append("")

    # 二、评估依据
    lines.append("## 二、评估依据")
    lines.append("")
    if evidence_basis:
        lines.append("| 序号 | 规范编号 | 规范名称 | 条款号 | 出处 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for idx, item in enumerate(evidence_basis, start=1):
            lines.append(
                f"| {idx} | {item.get('spec_code') or '-'} | {item.get('spec_name') or '-'} | "
                f"{item.get('clause_no') or '-'} | {item.get('location') or '-'} |"
            )
        lines.append("")
    else:
        lines.append("未检索到可用于判定的规范条款。")
        lines.append("")

    # 三、逐条比对
    lines.append("## 三、条款逐条比对")
    lines.append("")
    if matches:
        lines.append("| 序号 | 核查项 | 依据条款 | 判定 | 置信度 | 判定理由 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for idx, item in enumerate(matches, start=1):
            clause = item.get("clause_no") or item.get("spec_code") or "-"
            confidence = item.get("confidence")
            conf_text = f"{float(confidence):.2f}" if isinstance(confidence, (int, float)) else "-"
            reason = _escape_cell(str(item.get("reasoning") or ""))[:220]
            lines.append(
                f"| {idx} | {item.get('subtask_name') or '-'} | {clause} | "
                f"{verdict_label(item.get('verdict'))} | {conf_text} | {reason} |"
            )
        lines.append("")
        # 不符合项证据摘录
        problems = [m for m in matches if m.get("verdict") in (Verdict.NON_COMPLIANT.value, Verdict.PARTIAL.value)]
        if problems:
            lines.append("### 不符合项证据摘录")
            lines.append("")
            for item in problems:
                lines.append(f"- **{item.get('subtask_name') or '核查项'}**（{item.get('clause_no') or '-'}）")
                if item.get("evidence"):
                    lines.append(f"  - 证据：{str(item['evidence'])[:400]}")
                if item.get("reasoning"):
                    lines.append(f"  - 判定：{str(item['reasoning'])[:400]}")
            lines.append("")
    else:
        lines.append("本次评估未产生条款比对结果。")
        lines.append("")

    # 四、问题与整改
    analysis = report.get("analysis") or {}
    findings = analysis.get("findings") or []
    lines.append("## 四、问题清单与整改建议")
    lines.append("")
    if findings:
        lines.append("| 序号 | 依据条款 | 问题描述 | 原因分析 | 风险等级 | 整改建议 |")
        lines.append("| --- | --- | --- | --- | --- | --- |")
        for idx, item in enumerate(findings, start=1):
            lines.append(
                f"| {idx} | {item.get('clause_no') or '-'} | {_escape_cell(str(item.get('problem') or ''))} | "
                f"{_escape_cell(str(item.get('cause') or ''))} | {_risk_label(item.get('risk_level'))} | "
                f"{_escape_cell(str(item.get('remediation') or ''))} |"
            )
        lines.append("")
    else:
        lines.append("未发现不符合项，无需整改。")
        lines.append("")

    # 自定义章节：过滤掉与标准章节重复的标题，避免报告出现两个「一、评估概述」
    for section in report.get("sections") or []:
        heading = str(section.get("heading") or "").strip()
        content = str(section.get("content") or "").strip()
        if not heading or not content:
            continue
        if _is_standard_heading(heading):
            continue
        lines.append(f"## {heading}")
        lines.append("")
        lines.append(content)
        lines.append("")

    # 五、总体结论
    lines.append("## 五、总体结论")
    lines.append("")
    lines.append(f"- 总体判定：**{verdict_label(report.get('overall_verdict'))}**")
    lines.append(f"- 风险等级：**{_risk_label(report.get('risk_level'))}**")
    if analysis.get("summary"):
        lines.append(f"- 情况概述：{analysis['summary']}")
    if report.get("conclusion"):
        lines.append("")
        lines.append(str(report["conclusion"]))
    lines.append("")

    # 质量评审
    judge = report.get("judge")
    if judge:
        lines.append("## 六、智能质量评审（LLM-as-Judge）")
        lines.append("")
        lines.append(f"- 总评分：**{judge.get('total_score')}** / 5.00（{judge.get('grade_label') or judge.get('grade') or '-'}）")
        lines.append(f"- 评审模型：{judge.get('judge_model') or '-'}")
        if judge.get("needs_human"):
            lines.append("- ⚠️ 评分低于阈值或存在分歧，已转人工复核。")
        lines.append("")
        scores = judge.get("scores") or []
        if scores:
            lines.append("| 评分维度 | 得分 | 权重 | 评审意见 |")
            lines.append("| --- | --- | --- | --- |")
            for item in scores:
                lines.append(
                    f"| {item.get('dimension_label') or item.get('dimension')} | {item.get('score')} | "
                    f"{item.get('weight')} | {_escape_cell(str(item.get('comment') or ''))} |"
                )
            lines.append("")

    # 附录：引用溯源
    lines.append("## 附录：引用条款溯源")
    lines.append("")
    if matches:
        for idx, item in enumerate(matches, start=1):
            citation = item.get("citation") or {}
            loc = citation.get("location") or "-"
            lines.append(f"{idx}. {loc}（chunk #{citation.get('chunk_id') or '-'}）")
        lines.append("")
    lines.append("---")
    lines.append("")
    lines.append(DISCLAIMER)
    lines.append("")
    return "\n".join(lines)


def _is_standard_heading(heading: str) -> bool:
    """判断模型给出的章节标题是否与渲染器自带的标准章节重复。

    模型常把「一、评估概述」这类标准章节也放进 sections，直接渲染会导致报告
    出现重复章节（同一编号出现两次），因此按关键词做去重。
    """
    normalized = re.sub(r"^[一二三四五六七八九十\d\s、.．()（）]+", "", heading).strip()
    for keyword in _STANDARD_SECTION_KEYWORDS:
        if keyword in normalized or keyword in heading:
            return True
    return False


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|").replace("\n", " ").strip()


def _risk_label(level: Optional[str]) -> str:
    return {"high": "高", "medium": "中", "low": "低"}.get(str(level or "").lower(), "未评级")


def overall_verdict_from_matches(matches: Sequence[dict[str, Any]]) -> str:
    """由逐条判定推导总体判定：存在不符合→不符合；存在部分/证据不足→部分符合；否则符合。"""
    verdicts = [m.get("verdict") for m in matches]
    if not verdicts:
        return Verdict.INSUFFICIENT_EVIDENCE.value
    if Verdict.NON_COMPLIANT.value in verdicts:
        return Verdict.NON_COMPLIANT.value
    if Verdict.PARTIAL.value in verdicts or Verdict.INSUFFICIENT_EVIDENCE.value in verdicts:
        return Verdict.PARTIAL.value
    if all(v == Verdict.NOT_APPLICABLE.value for v in verdicts):
        return Verdict.NOT_APPLICABLE.value
    return Verdict.COMPLIANT.value


def risk_from_matches(matches: Sequence[dict[str, Any]]) -> str:
    levels = [str(m.get("risk_level") or "").lower() for m in matches]
    if "high" in levels:
        return "high"
    if "medium" in levels:
        return "medium"
    return "low"


__all__ = [
    "render_markdown",
    "verdict_label",
    "overall_verdict_from_matches",
    "risk_from_matches",
    "DISCLAIMER",
    "VERDICT_RANK",
]
