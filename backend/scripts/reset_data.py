# -*- coding: utf-8 -*-
"""清空业务数据并重建初始数据（清库重建）。

背景
----
一条命令把系统恢复到「刚部署完、只有示例规范」的干净状态，
避免手工 TRUNCATE 时漏删（外键报错）或误删账号/项目（导致无法登录、
任务归属失效）。

清理范围（**明确区分**，不接受「全删」）
--------------------------------------
保留（系统骨架，删了会导致无法登录或任务丢失归属）：

- ``sys_user``       账号（admin / engineer / expert / kb_manager / viewer）
- ``project``        项目（数据隔离依据）
- ``system_config``  运行配置
- ``alembic_version`` 迁移版本（必须保留，否则下次启动会重复建表）

清理（业务数据，可重建）：

- 评估链路：``eval_task`` / ``eval_subtask`` / ``match_result`` /
  ``eval_report`` / ``judge_review`` / ``judge_score`` / ``human_feedback``
- 执行留痕：``agent_step_log`` / ``agent_tool_call`` / ``llm_call_log`` / ``audit_log``
- 规范内容：``spec_doc`` / ``doc_version`` / ``doc_chunk`` / ``clause_ref``
- LangGraph 检查点：``checkpoints`` / ``checkpoint_blobs`` / ``checkpoint_writes``

**同时清理检索与图谱**：``KNOWLEDGE_BASE`` 保留（它是索引命名空间 ``kb_{id}`` 的
依据），但会删除该命名空间下的 FAISS / BM25 索引文件与 Neo4j 图谱节点，
再由种子脚本重建 —— 否则会留下「库中无文档、索引里还有向量」的孤儿数据。

用法
----
    python scripts/reset_data.py --dry-run   # 只看会删什么（默认行为）
    python scripts/reset_data.py --yes       # 真正执行
    python scripts/reset_data.py --yes --keep-audit   # 保留审计日志
    python scripts/reset_data.py --yes --skip-seed    # 清完不重建（仅清库）
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, "/app")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

#: 清理顺序：从叶到根。外键依赖决定顺序，不能随意调换。
#: （依赖关系见 scripts/_survey_db.py 的输出）
DELETE_ORDER: list[str] = [
    # --- 评审与反馈（依赖 eval_report / eval_task）---
    "judge_score",
    "judge_review",
    "human_feedback",
    "eval_report",
    # --- 任务子表（依赖 eval_task）---
    "eval_subtask",
    "match_result",
    "agent_step_log",
    "agent_tool_call",
    # --- 任务主体 ---
    "eval_task",
    # --- 规范内容（依赖 spec_doc / doc_version）---
    "doc_chunk",
    "clause_ref",
    "doc_version",
    "spec_doc",
    # --- 留痕 ---
    "llm_call_log",
    "audit_log",
]

#: LangGraph 检查点表（不在 ORM 元数据里，需显式列出）
CHECKPOINT_TABLES: list[str] = [
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoints",
]

#: 保留的表（仅用于打印说明）
KEEP_TABLES = ["sys_user", "project", "knowledge_base", "system_config", "alembic_version"]


def _count(session, table: str) -> int:
    from sqlalchemy import func, select

    from app.db.models import Base

    if table not in Base.metadata.tables:
        return 0
    model = Base.metadata.tables[table]
    return int(session.execute(select(func.count()).select_from(model)).scalar() or 0)


def survey() -> dict[str, int]:
    from app.db.session import session_scope

    with session_scope() as session:
        return {t: _count(session, t) for t in DELETE_ORDER + CHECKPOINT_TABLES}


def clear_database(keep_audit: bool) -> dict[str, int]:
    """按依赖顺序删除业务数据，返回各表删除行数。"""
    from sqlalchemy import text

    from app.db.session import session_scope

    removed: dict[str, int] = {}
    tables = [t for t in DELETE_ORDER if not (keep_audit and t == "audit_log")]
    tables += CHECKPOINT_TABLES

    with session_scope() as session:
        for table in tables:
            # SQL 标识符不能用绑定参数，这里表名来自代码常量（非用户输入）
            result = session.execute(text(f'DELETE FROM "{table}"'))
            removed[table] = result.rowcount or 0
    return removed


def clear_vector_and_graph(verbose: bool = True) -> dict[str, int]:
    """清理向量索引文件与图谱节点（知识库本身保留）。

    为什么必须一起清：``doc_chunk`` 没了但 FAISS 索引还在，检索会召回
    「不存在的条款」——比没有索引更糟（会产出幻觉引用）。
    """
    stats = {"namespaces": 0, "files": 0, "graph_nodes": 0}

    var_dir = Path("/app/var")
    faiss_dir = var_dir / "faiss"
    if faiss_dir.exists():
        for child in faiss_dir.iterdir():
            if child.is_dir() and child.name.startswith("kb_"):
                stats["namespaces"] += 1
            elif child.is_file() and child.name.startswith(("bm25_", "index_")):
                stats["files"] += 1
            else:
                continue
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)
        if verbose:
            print(f"  向量索引：清理 {stats['namespaces']} 个命名空间目录、{stats['files']} 个索引文件")

    # 存储的原始上传文件（种子示例不经过上传，清掉不影响的）
    storage_dir = var_dir / "storage"
    if storage_dir.exists():
        for child in storage_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
            else:
                child.unlink(missing_ok=True)

    # Neo4j 图谱
    try:
        from app.kg.graph_store import get_graph_store

        store = get_graph_store()
        if getattr(store, "available", False):
            before = store.stats()
            store.clear()
            after = store.stats()
            stats["graph_nodes"] = max(
                0,
                int(before.get("nodes", 0) if isinstance(before, dict) else 0)
                - int(after.get("nodes", 0) if isinstance(after, dict) else 0),
            )
            if verbose:
                print(f"  知识图谱：已清空（原有 {before.get('nodes', '?')} 个节点）")
        elif verbose:
            print("  知识图谱：未启用，跳过")
    except Exception as exc:  # noqa: BLE001
        print(f"  [警告] 图谱清理失败（不影响后续重建）：{type(exc).__name__}: {exc}")

    return stats


def rebuild_graph() -> int:
    """重建知识图谱（委托 ``rebuild_graph.py``，避免两处实现分叉）。

    ⚠️ ``seed_data.py`` **只写文档与向量索引，不建图谱** ——
    不重建会留下「库里 4 部规范、图谱里全是旧条款」的孤儿数据，
    且现象隐蔽：图谱增强检索变差、引用链为空，但界面不报错。
    """
    import subprocess

    script = Path(__file__).with_name("rebuild_graph.py")
    print(f"  执行 {script.name} …")
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=3600,
    )
    for line in (result.stdout or "").splitlines():
        if line.strip():
            print(f"    {line.strip()}")
    if result.returncode != 0:
        print("  [警告] 图谱未完全重建，图谱增强检索可能不可用")
    return result.returncode


def rebuild() -> int:
    """运行种子脚本重建示例规范、向量索引与图谱。"""
    import subprocess

    script = Path(__file__).with_name("seed_data.py")
    print(f"  执行 {script.name} …")
    result = subprocess.run(
        [sys.executable, str(script)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=1800,
    )
    for line in (result.stdout or "").splitlines():
        if line.strip():
            print(f"    {line.strip()}")
    if result.returncode != 0:
        print(f"  [X] 重建失败（exit={result.returncode}）")
        for line in (result.stderr or "").splitlines()[-8:]:
            print(f"    {line}")
        return 1
    return 0


def print_summary() -> None:
    from sqlalchemy import func, inspect, select

    from app.db.models import Base
    from app.db.session import session_scope

    with session_scope() as session:
        inspector = inspect(session.get_bind())
        existing = set(inspector.get_table_names())
        print("  === 重建后各表数据量 ===")
        total = 0
        for table in sorted(Base.metadata.tables):
            if table not in existing:
                continue
            model = Base.metadata.tables[table]
            count = int(session.execute(select(func.count()).select_from(model)).scalar() or 0)
            total += count
            if count:
                print(f"    {table:24s} {count:5d}")
        print(f"    {'合计':24s} {total:5d}")


def main() -> int:
    parser = argparse.ArgumentParser(description="清空业务数据并重建初始数据")
    parser.add_argument("--yes", action="store_true", help="确认执行（不加此参数只做预览）")
    parser.add_argument("--keep-audit", action="store_true", help="保留审计日志")
    parser.add_argument("--skip-seed", action="store_true", help="清完不重建（仅清库）")
    parser.add_argument("--skip-graph", action="store_true", help="不重建知识图谱")
    args = parser.parse_args()

    print("=== 当前数据量 ===")
    before = survey()
    for table, count in before.items():
        if count:
            print(f"  {table:24s} {count:5d}")

    if not args.yes:
        print()
        print("=== 将执行的操作（未执行，加 --yes 才真正清理）===")
        print("  清理（业务数据，可重建）：")
        for table in DELETE_ORDER:
            if args.keep_audit and table == "audit_log":
                continue
            print(f"    - {table}")
        print("  清理（LangGraph 检查点）：")
        for table in CHECKPOINT_TABLES:
            print(f"    - {table}")
        print("  保留（系统骨架）：")
        for table in KEEP_TABLES:
            print(f"    + {table}")
        print("  另清理：FAISS/BM25 索引文件、storage 上传文件、Neo4j 图谱节点")
        if not args.skip_seed:
            print("  然后运行 seed_data.py 重建示例规范、索引与图谱")
        print()
        print("[DRY-RUN] 未做任何修改")
        return 0

    print()
    print("=== 1) 清理数据库业务数据 ===")
    removed = clear_database(args.keep_audit)
    for table, count in removed.items():
        if count:
            print(f"  已删 {table:24s} {count:5d}")
    if args.keep_audit:
        print("  （按 --keep-audit 保留 audit_log）")

    print()
    print("=== 2) 清理向量索引、上传文件与知识图谱 ===")
    clear_vector_and_graph()

    code = 0
    if args.skip_seed:
        print()
        print("=== 3) 跳过重建（--skip-seed）===")
    else:
        print()
        print("=== 3) 重建初始数据 ===")
        code = rebuild()

        # 图谱必须单独重建：seed_data.py 不建图谱。
        # 不清不建会留下「库里无文档、图谱里全是旧条款」的孤儿数据，
        # 而且现象隐蔽（检索变差、引用链为空，但界面不报错）。
        if code == 0 and not args.skip_graph:
            print()
            print("=== 3b) 重建知识图谱 ===")
            graph_code = rebuild_graph()
            if graph_code != 0:
                print("  [警告] 图谱未完全重建，图谱增强检索可能不可用")
                code = 0  # 图谱失败不阻断整体（文档与索引已可用）

    print()
    print("=== 4) 重建结果 ===")
    print_summary()

    if code == 0:
        print()
        print("[OK] 清库重建完成")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
