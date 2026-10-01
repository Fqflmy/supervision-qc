# -*- coding: utf-8 -*-
"""Neo4j 图谱连通性与引用链自检（FR-KG-01~05）。

不依赖大模型：直接写入样例条款与引用关系，然后验证引用链、存在性校验与冲突检测。
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

os.environ.setdefault("SUPERVISION_NEO4J_ENABLED", "true")
os.environ.setdefault("SUPERVISION_LOG_LEVEL", "ERROR")
os.environ.setdefault("SUPERVISION_LOG_JSON", "false")

from app.kg.graph_store import get_graph_store  # noqa: E402


def main() -> int:
    store = get_graph_store()
    print(f"Neo4j 可用: {store.available}")
    if not store.available:
        print("[FAIL] Neo4j 不可用，请检查 SUPERVISION_NEO4J_URI 与容器状态")
        return 2

    store.init_schema()
    print("约束与索引已创建")

    # 构造最小引用链：GB 50204-2015 5.3.3 <- 6.2.1（细化）<- GB 50300-2013 3.0.7（引用）
    store.upsert_spec("GB 50204-2015", "混凝土结构工程施工质量验收规范", specialty="结构工程")
    store.upsert_spec("GB 50300-2013", "建筑工程施工质量验收统一标准", specialty="通用")
    clauses = [
        (9001, "5.3.3", "GB 50204-2015", "混凝土浇筑时的入模温度不宜高于30℃，不宜低于5℃。"),
        (9002, "6.2.1", "GB 50204-2015", "现浇结构的外观质量不宜有一般缺陷，应按技术处理方案处理。"),
        (9003, "3.0.7", "GB 50300-2013", "检验批合格质量标准：主控项目均应合格，一般项目经抽样检验合格。"),
        (9004, "5.2.1", "GB 50204-2015", "水泥进场时应对其品种、级别、出厂日期等进行检查并复验。"),
    ]
    for clause_id, clause_no, spec_code, content in clauses:
        store.upsert_clause(
            clause_id=clause_id, clause_no=clause_no, spec_code=spec_code, content=content
        )
    print(f"已写入条款节点 {len(clauses)} 个")

    linked = 0
    for src, dst, relation in (
        ("5.3.3", "6.2.1", "REFINES"),
        ("6.2.1", "3.0.7", "REFERENCES"),
        ("5.2.1", "3.0.7", "REFERENCES"),
    ):
        if store.link_clauses(src, dst, relation, confidence=0.9, evidence="自检样例"):
            linked += 1
    print(f"已建立引用关系 {linked} 条")

    for entity, clause_no in (("混凝土入模温度", "5.3.3"), ("现浇结构外观质量", "6.2.1")):
        store.upsert_entity(entity, "Indicator")
        store.link_entity_to_clause(entity, clause_no, "DEFINES")
    print("已写入实体并关联条款")

    stats = store.stats()
    print(f"图谱统计: {stats}")
    assert stats.get("clauses", 0) >= len(clauses)
    assert stats.get("references", 0) >= 1

    print("\n--- 引用链追踪（深度 2，双向）---")
    chain = store.reference_chain("5.3.3", direction="both", depth=2)
    for edge in chain["edges"]:
        print(f"  {edge['source']} --{edge['relations']}--> {edge['target']}")
    assert chain["edges"], "5.3.3 应能追踪到关联条款"
    assert any(edge["target"] == "6.2.1" for edge in chain["edges"]), "应包含 REFINES 关系"

    print("\n--- 邻居扩展（检索增强用）---")
    neighbors = store.neighbors("3.0.7", depth=2, limit=5)
    for item in neighbors:
        print(f"  {item['clause_no']} ({item['spec_code']}) 关系={item['relations']}")
    assert neighbors, "3.0.7 应有反向引用邻居"

    print("\n--- 条款存在性校验（幻觉引用识别）---")
    assert store.clause_exists("5.3.3") is True
    assert store.clause_exists("99.9.9") is False
    existing = store.existing_clause_nos(["5.3.3", "3.0.7", "88.8.8"])
    assert existing == {"5.3.3", "3.0.7"}, f"批量校验结果异常：{existing}"
    print(f"  已存在: {sorted(existing)}；88.8.8 判定为不存在（幻觉）")

    print("\n--- 可视化子图 ---")
    subgraph = store.subgraph(["5.3.3", "6.2.1"], limit=50)
    print(f"  节点 {len(subgraph['nodes'])} 个，边 {len(subgraph['edges'])} 条")
    assert subgraph["nodes"], "子图不应为空"

    print("\n[OK] 知识图谱自检通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
