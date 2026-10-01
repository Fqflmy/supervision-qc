# -*- coding: utf-8 -*-
"""生成一份「交付样本」评估：真实模型跑完整 Agent 流程并导出报告。

用于给业务方演示真实输出质量，或作为人工复核的样例归档。

用法：
    python scripts/make_sample_report.py                 # 使用 .env 配置的模型
    python scripts/make_sample_report.py --fake          # 离线 Fake 模型（无 Key 也能跑）
    python scripts/make_sample_report.py --out D:/reports
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

SAMPLE = {
    "title": "交付样本·地下室剪力墙混凝土施工质量评估",
    "eval_type": "inspection_lot",
    "specialty": "结构工程",
    "object": {
        "part": "地下室剪力墙",
        "project_name": "示范工程·某住宅小区 1# 楼",
        "description": "夏季高温季节施工，混凝土入模温度偏高",
        "records": [
            {
                "name": "混凝土浇筑记录",
                "content": "C30 混凝土入模温度 32℃，连续浇筑 6 小时，坍落度 180mm，留置标准养护试件 3 组。",
            },
            {
                "name": "养护记录",
                "content": "浇筑完成后 24h 开始浇水养护，累计养护 5 天，未留置同条件养护试件。",
            },
            {
                "name": "原材料检验记录",
                "content": "水泥出厂合格证齐全，进场复验报告已提供；砂石、外加剂进场复验报告未见。",
            },
            {
                "name": "隐蔽工程验收记录",
                "content": "钢筋安装隐蔽验收记录已签认，保护层厚度检测报告未提供。",
            },
        ],
    },
    "options": {},
}


async def run(fake: bool, out_dir: Path) -> int:
    if fake:
        os.environ["SUPERVISION_LLM_PROVIDER"] = "fake"
    os.environ.setdefault("SUPERVISION_EMBEDDING_PROVIDER", "hash")
    os.environ.setdefault("SUPERVISION_RERANKER_PROVIDER", "score_fusion")

    from app.agent.runner import run_evaluation
    from app.core.logging_conf import setup_logging
    from app.db import session_scope
    from app.db.models import EvalTask

    setup_logging("WARNING", json_output=False)

    with session_scope() as session:
        task = EvalTask(
            eval_type=SAMPLE["eval_type"],
            specialty=SAMPLE["specialty"],
            title=SAMPLE["title"],
            input_payload=SAMPLE["object"],
            options=SAMPLE["options"],
        )
        session.add(task)
        session.flush()
        task_id = task.id
        result = await run_evaluation(session, task)

    print(f"任务：{task_id}")
    print(f"状态：{result['current_state']}  迭代：{result['iteration_count']}  "
          f"子任务：{len(result['subtasks'])}  条款比对：{len(result['matches'])}")
    print(f"Token：{result['token_used']}  耗时：{result['elapsed_ms'] / 1000:.1f}s  "
          f"检查点：{result['checkpoint_backend']}")
    print(f"报告：{len(result['markdown'])} 字  总体判定：{(result['report'] or {}).get('overall_verdict')}")

    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"sample-report-{str(task_id)[:8]}.md"
    path.write_text(result["markdown"] or "", encoding="utf-8")
    print(f"\n报告已导出：{path}")
    print(f"（如需触发质量评审：POST /api/v1/judge/reports/{{report_id}}/score，"
          f"report_id={result.get('report_id')}）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="生成交付样本评估报告")
    parser.add_argument("--fake", action="store_true", help="使用离线 Fake 模型")
    parser.add_argument("--out", default=str(BACKEND.parent / "var" / "artifacts"))
    args = parser.parse_args()
    return asyncio.run(run(args.fake, Path(args.out)))


if __name__ == "__main__":
    raise SystemExit(main())
