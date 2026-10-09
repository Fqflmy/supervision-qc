# -*- coding: utf-8 -*-
"""知识图谱节点标签隔离（由「4 部规范却报 12 个 Spec 节点」挖出的缺陷）。

背景
----
``EvalReport`` 之外的图谱也有类似「一个标签两种含义」的问题：

- ``upsert_spec`` 写 ``(:Spec {spec_code, spec_name})`` —— 规范节点；
- ``upsert_entity`` 把 LLM 抽出的实体也打上 ``label`` 作为 Neo4j 标签，
  而 LLM 完全可能把文中提到的规范名称标成 ``Spec``。

两者挤在同一标签下，属性结构不同，导致 ``stats()`` 按标签计数虚高：
实测库里 4 部规范、图谱却报 **12 个 Spec 节点**（4 个真规范 + 8 个实体）。

修法：实体不得占用有专用节点类型的保留标签（``Spec`` / ``Clause`` / ``Chapter``），
一律降级为 ``Entity``；实体语义仍保留在 ``label`` 属性中。
"""
from __future__ import annotations

import pytest


def test_reserved_labels_are_declared():
    """保留标签必须显式声明，避免后续新增专用节点类型时漏加。"""
    from app.kg.graph_store import RESERVED_NODE_LABELS

    assert {"Spec", "Clause", "Chapter"} <= RESERVED_NODE_LABELS, (
        "有专用节点类型的标签未声明为保留标签"
    )


def test_entity_cannot_claim_reserved_label():
    """实体声称自己是 Spec/Clause/Chapter 时，节点标签必须降级为 Entity。"""
    from app.kg.graph_store import RESERVED_NODE_LABELS, get_graph_store

    store = get_graph_store()
    if not store.available:
        pytest.skip("Neo4j 不可用，跳过图谱标签测试")

    text = "__pytest_保留标签降级__"
    try:
        for label in sorted(RESERVED_NODE_LABELS):
            store.upsert_entity(text, label)
            rows = store._run(
                f"MATCH (e:Entity {{text: '{text}'}}) RETURN labels(e) AS labels"
            )
            labels = rows[0]["labels"] if rows else []
            leaked = [lbl for lbl in labels if lbl in RESERVED_NODE_LABELS]
            assert not leaked, (
                f"实体占用保留标签 {label} 未被降级：节点标签={labels}"
            )
            store._run(f"MATCH (e:Entity {{text: '{text}'}}) DELETE e")
    finally:
        store._run(f"MATCH (e:Entity {{text: '{text}'}}) DELETE e")


def test_entity_keeps_non_reserved_label():
    """无专用节点类型的标签应原样保留，否则丢失实体语义。"""
    from app.kg.graph_store import get_graph_store

    store = get_graph_store()
    if not store.available:
        pytest.skip("Neo4j 不可用，跳过图谱标签测试")

    text = "__pytest_普通标签保留__"
    try:
        store.upsert_entity(text, "Material")
        rows = store._run(f"MATCH (e:Entity {{text: '{text}'}}) RETURN labels(e) AS labels")
        labels = rows[0]["labels"] if rows else []
        assert "Material" in labels, f"普通标签被误降级：{labels}"
        # 语义仍需在 label 属性中可查
        rows2 = store._run(f"MATCH (e:Entity {{text: '{text}'}}) RETURN e.label AS label")
        assert rows2 and rows2[0]["label"] == "Material"
    finally:
        store._run(f"MATCH (e:Entity {{text: '{text}'}}) DELETE e")


def test_graph_spec_count_matches_database(requires_db):
    """图谱 Spec 节点数必须与库中规范数一致（这正是当初报 12 的原因）。"""
    from sqlalchemy import func, select

    from app.db.models import SpecDoc
    from app.db.session import session_scope
    from app.kg.graph_store import get_graph_store

    store = get_graph_store()
    if not store.available:
        pytest.skip("Neo4j 不可用，跳过图谱一致性测试")

    with session_scope() as session:
        db_specs = int(session.execute(select(func.count(SpecDoc.id))).scalar() or 0)

    stats = store.stats()
    graph_specs = int(stats.get("specs", 0))

    assert graph_specs == db_specs, (
        f"图谱 Spec 节点数 {graph_specs} 与库中规范数 {db_specs} 不一致 —— "
        "很可能实体又占用了 Spec 标签（应为 1 比 1）"
    )
