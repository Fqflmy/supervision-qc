# -*- coding: utf-8 -*-
"""导出评估报告为 Markdown 文件（用于交付、评审归档或人工复核）。

用法：
    python scripts/export_report.py                      # 导出最新一份报告
    python scripts/export_report.py --latest-longest     # 导出最长的一份（通常是真实模型产出）
    python scripts/export_report.py --task-id <uuid>     # 导出指定任务
    python scripts/export_report.py --out D:/reports     # 指定输出目录
"""
from __future__ import annotations

import argparse
import re
import sys
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from sqlalchemy import func, select  # noqa: E402

from app.db import session_scope  # noqa: E402
from app.db.models import EvalReport, EvalTask, SpecDoc  # noqa: E402

DEFAULT_OUT = BACKEND.parent / "var" / "artifacts"


def safe_name(text: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "_", text)[:80] or "report"


def main() -> int:
    parser = argparse.ArgumentParser(description="导出评估报告")
    parser.add_argument("--task-id", help="按任务 ID 导出（支持前缀）")
    parser.add_argument("--title", help="按任务标题关键字过滤")
    parser.add_argument("--latest-longest", action="store_true", help="导出篇幅最长的一份")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="输出目录")
    parser.add_argument("--all", action="store_true", help="导出全部报告")
    args = parser.parse_args()

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []

    with session_scope() as session:
        stmt = select(EvalReport).order_by(EvalReport.created_at.desc())
        if args.latest_longest:
            stmt = select(EvalReport).order_by(func.length(EvalReport.markdown).desc())
        reports = session.execute(stmt).scalars().all()

        if args.task_id:
            prefix = args.task_id.strip().lower()
            reports = [r for r in reports if str(r.task_id).lower().startswith(prefix)]
        if args.title:
            matched = []
            for report in reports:
                task = session.get(EvalTask, report.task_id)
                title = (task.title if task else "") or ""
                if args.title in title:
                    matched.append(report)
            reports = matched

        if not reports:
            print("[FAIL] 没有匹配的报告，请检查 --task-id / --title，或先执行评估任务")
            return 2
        if not (args.all or args.task_id or args.title):
            reports = reports[:1]

        for report in reports:
            task = session.get(EvalTask, report.task_id)
            title = (task.title if task else None) or report.overall_verdict or "评估报告"
            name = f"report-{str(report.id)[:8]}-{safe_name(title)}.md"
            path = out_dir / name
            path.write_text(report.markdown or "（报告内容为空）", encoding="utf-8")
            written.append(path)
            print(
                f"[OK] {name}\n"
                f"     篇幅={len(report.markdown or '')} 字 生成模型={report.generator_model} "
                f"总体判定={report.overall_verdict} 风险={report.risk_level} "
                f"依据={report.basis_count} 问题={report.non_compliance_count}"
            )

    print(f"\n共导出 {len(written)} 份报告到 {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
