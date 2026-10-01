# -*- coding: utf-8 -*-
"""Neo4j 图谱存储（SRS 5.4）。

节点：Spec / Chapter / Clause / Term / Part / Material / Indicator / Org
关系：CONTAINS / REFERENCES / SUPERSEDES / REFINES / CONFLICTS_WITH / APPLIES_TO / DEFINES / ISSUED_BY

关键能力：
- 条款→原文块双向映射（clause_id 为唯一键）；
- 引用链追踪（正向/反向，深度可配，FR-KG-04）；
- 条款存在性校验，供 Judge 识别幻觉引用（FR-JDG-03）；
- 服务不可用时 available=False，调用方自动跳过图谱增强，不阻塞主链路。
"""
from __future__ import annotations

import threading
import time
from typing import Any, Iterable, Optional, Sequence

from app.config import settings
from app.constants import KgRelation
from app.core.logging_conf import get_logger

logger = get_logger(__name__)

ALLOWED_RELATIONS = {r.value for r in KgRelation}

CONSTRAINTS = (
    "CREATE CONSTRAINT clause_id IF NOT EXISTS FOR (c:Clause) REQUIRE c.clause_id IS UNIQUE",
    "CREATE CONSTRAINT spec_code IF NOT EXISTS FOR (s:Spec) REQUIRE s.spec_code IS UNIQUE",
    "CREATE CONSTRAINT term_key IF NOT EXISTS FOR (t:Term) REQUIRE t.term_key IS UNIQUE",
    "CREATE INDEX clause_no_idx IF NOT EXISTS FOR (c:Clause) ON (c.clause_no)",
    "CREATE INDEX entity_text_idx IF NOT EXISTS FOR (e:Entity) ON (e.text)",
)


class GraphStore:
    """Neo4j 驱动封装（线程安全，懒连接）。

    可用性自愈：容器化部署时 Neo4j 往往比 api 晚就绪，若首次探测失败就永久
    标记不可用，图谱增强会被长期关闭。因此失败后按 ``_recheck_interval`` 周期重试。
    """

    #: 探测失败后的重试间隔（秒）
    _recheck_interval = 30.0

    def __init__(self) -> None:
        self._driver = None
        # 必须是可重入锁：available 持锁后会调用 _connect()，而 _connect() 也要
        # 取同一把锁；用普通 Lock 会自死锁，使所有依赖图谱的请求永久挂起。
        self._lock = threading.RLock()
        self._checked = False
        self._available = False
        self._last_check_at = 0.0
        self._last_error: Optional[str] = None
        # 连通性探测锁：保证同一时刻只有一个线程真正去连 Neo4j，
        # 其他线程不等待，直接返回上次结果（避免健康检查被拖死）。
        self._probe_lock = threading.Lock()

    # ------------------------------------------------------------------ #
    # 连接
    # ------------------------------------------------------------------ #
    def _connect(self):
        with self._lock:
            if self._driver is None:
                from neo4j import GraphDatabase

                self._driver = GraphDatabase.driver(
                    settings.neo4j_uri,
                    auth=(settings.neo4j_user, settings.neo4j_password),
                    max_connection_lifetime=settings.neo4j_max_conn_lifetime,
                    connection_timeout=settings.db_connect_timeout,
                    # 图谱为空时 Cypher 会返回「标签/属性不存在」通知，属于预期状态；
                    # neo4j 6.x 的可选等级只有 OFF/WARNING/INFORMATION，这里直接关闭通知。
                    notifications_min_severity="OFF",
                )
            return self._driver

    @property
    def available(self) -> bool:
        if not settings.neo4j_enabled:
            return False
        if self._checked and self._available:
            return True
        # 未检测过，或上次失败且已过重试间隔 → 重新探测（自愈）
        if self._checked and (time.monotonic() - self._last_check_at) < self._recheck_interval:
            return False
        # 非阻塞抢占探测权：已有线程在探测时直接返回上次结果，
        # 不能让健康检查/检索请求排队等在 Neo4j 连接上。
        if not self._probe_lock.acquire(blocking=False):
            return self._available
        try:
            if self._checked and (time.monotonic() - self._last_check_at) < self._recheck_interval:
                return self._available
            self._checked = True
            self._last_check_at = time.monotonic()
            try:
                self._connect().verify_connectivity()
                if not self._available:
                    logger.info("Neo4j 连接就绪", extra={"uri": settings.neo4j_uri})
                self._available = True
                self._last_error = None
            except Exception as exc:  # noqa: BLE001
                if self._available or self._last_error is None:
                    logger.warning(
                        "Neo4j 不可用，图谱增强功能自动关闭（将周期重试）",
                        extra={"uri": settings.neo4j_uri, "error": str(exc)[:200]},
                    )
                self._available = False
                self._last_error = str(exc)[:200]
        finally:
            self._probe_lock.release()
        return self._available

    def reset_availability(self) -> None:
        """强制下次访问时重新探测。"""
        self._checked = False
        self._available = False
        self._last_check_at = 0.0

    def close(self) -> None:
        with self._lock:
            if self._driver is not None:
                self._driver.close()
                self._driver = None
        self._checked = False
        self._available = False
        self._last_check_at = 0.0

    def _run(self, cypher: str, **params: Any) -> list[dict]:
        driver = self._connect()
        with driver.session(database=settings.neo4j_database) as session:
            result = session.run(cypher, **params)
            return [record.data() for record in result]

    # ------------------------------------------------------------------ #
    # 初始化与写入
    # ------------------------------------------------------------------ #
    def init_schema(self) -> bool:
        if not self.available:
            return False
        for statement in CONSTRAINTS:
            try:
                self._run(statement)
            except Exception as exc:  # noqa: BLE001
                logger.warning("图谱约束创建失败", extra={"statement": statement, "error": str(exc)[:160]})
        return True

    def upsert_spec(self, spec_code: str, spec_name: str, **extra: Any) -> None:
        if not self.available:
            return
        self._run(
            """
            MERGE (s:Spec {spec_code: $spec_code})
            SET s.spec_name = $spec_name,
                s.specialty = coalesce($specialty, s.specialty),
                s.region_level = coalesce($region_level, s.region_level),
                s.status = coalesce($status, s.status),
                s.updated_at = timestamp()
            """,
            spec_code=spec_code,
            spec_name=spec_name,
            specialty=extra.get("specialty"),
            region_level=extra.get("region_level"),
            status=extra.get("status"),
        )

    def upsert_clause(
        self,
        clause_id: int,
        clause_no: Optional[str],
        spec_code: Optional[str],
        content: str,
        *,
        chapter_path: Optional[str] = None,
        page_no: Optional[int] = None,
        chunk_id: Optional[int] = None,
        doc_id: Optional[int] = None,
        embedding: Optional[Sequence[float]] = None,
    ) -> None:
        """写入/更新条款节点，并建立 Clause-[:BELONGS_TO]->Spec 关系。"""
        if not self.available:
            return
        self._run(
            """
            MERGE (c:Clause {clause_id: $clause_id})
            SET c.clause_no = $clause_no,
                c.content = $content,
                c.chapter_path = $chapter_path,
                c.page_no = $page_no,
                c.chunk_id = $chunk_id,
                c.doc_id = $doc_id,
                c.spec_code = $spec_code,
                c.embedding = $embedding,
                c.updated_at = timestamp()
            WITH c
            OPTIONAL MATCH (s:Spec {spec_code: $spec_code})
            FOREACH (_ IN CASE WHEN s IS NULL THEN [] ELSE [1] END |
                MERGE (c)-[:BELONGS_TO]->(s)
            )
            """,
            clause_id=int(clause_id),
            clause_no=clause_no,
            content=(content or "")[:4000],
            chapter_path=chapter_path,
            page_no=page_no,
            chunk_id=chunk_id,
            doc_id=doc_id,
            spec_code=spec_code,
            embedding=list(embedding) if embedding else None,
        )

    def upsert_entity(self, text: str, label: str, **extra: Any) -> None:
        if not self.available or not text:
            return
        label = label if label.isalnum() else "Entity"
        self._run(
            f"""
            MERGE (e:Entity {{text: $text}})
            SET e.label = $label, e.updated_at = timestamp()
            SET e:{label}
            """,
            text=text,
            label=label,
        )

    def link_clauses(
        self,
        src_clause_no: Optional[str],
        dst_clause_no: Optional[str],
        relation: str,
        *,
        src_spec_code: Optional[str] = None,
        dst_spec_code: Optional[str] = None,
        confidence: Optional[float] = None,
        evidence: Optional[str] = None,
    ) -> bool:
        """建立条款间引用关系（FR-KG-02/04）。"""
        if not self.available:
            return False
        relation = (relation or KgRelation.REFERENCES.value).upper()
        if relation not in ALLOWED_RELATIONS:
            relation = KgRelation.REFERENCES.value
        if not src_clause_no or not dst_clause_no:
            return False
        self._run(
            f"""
            MATCH (a:Clause {{clause_no: $src}})
            MATCH (b:Clause {{clause_no: $dst}})
            MERGE (a)-[r:{relation}]->(b)
            SET r.confidence = coalesce($confidence, r.confidence),
                r.evidence = coalesce($evidence, r.evidence),
                r.src_spec_code = coalesce($src_spec, r.src_spec_code),
                r.dst_spec_code = coalesce($dst_spec, r.dst_spec_code),
                r.updated_at = timestamp()
            """,
            src=src_clause_no,
            dst=dst_clause_no,
            confidence=confidence,
            evidence=(evidence or "")[:500] or None,
            src_spec=src_spec_code,
            dst_spec=dst_spec_code,
        )
        return True

    def link_entity_to_clause(
        self, entity_text: str, clause_no: Optional[str], relation: str = "DEFINES"
    ) -> None:
        if not self.available or not entity_text or not clause_no:
            return
        relation = relation if relation in ALLOWED_RELATIONS else "DEFINES"
        self._run(
            f"""
            MATCH (c:Clause {{clause_no: $clause_no}})
            MATCH (e:Entity {{text: $text}})
            MERGE (c)-[:{relation}]->(e)
            """,
            clause_no=clause_no,
            text=entity_text,
        )

    # ------------------------------------------------------------------ #
    # 查询
    # ------------------------------------------------------------------ #
    def neighbors(
        self, clause_no: Optional[str], *, depth: int = 1, limit: int = 5
    ) -> list[dict[str, Any]]:
        """取条款的关联条款（用于检索增强，FR-KG-05）。"""
        if not self.available or not clause_no:
            return []
        depth = max(1, min(int(depth), settings.neo4j_ref_max_depth))
        try:
            records = self._run(
                f"""
                MATCH (c:Clause {{clause_no: $clause_no}})-[r*1..{depth}]-(n:Clause)
                WHERE n.clause_no IS NOT NULL AND n.clause_no <> $clause_no
                RETURN DISTINCT n.clause_no AS clause_no,
                       n.spec_code AS spec_code,
                       n.content AS content,
                       [rel IN r | type(rel)] AS relations
                LIMIT $limit
                """,
                clause_no=clause_no,
                limit=int(limit),
            )
            return [
                {
                    "clause_no": rec.get("clause_no"),
                    "spec_code": rec.get("spec_code"),
                    "relations": rec.get("relations") or [],
                    "preview": (rec.get("content") or "")[:120],
                }
                for rec in records
            ]
        except Exception as exc:  # noqa: BLE001
            logger.warning("图谱邻居查询失败", extra={"clause_no": clause_no, "error": str(exc)[:160]})
            return []

    def reference_chain(
        self,
        clause_no: str,
        *,
        direction: str = "both",
        depth: Optional[int] = None,
        limit: int = 100,
    ) -> dict[str, Any]:
        """引用链追踪（FR-KG-04）：正向 REFERENCES、反向被引用。"""
        depth = depth or settings.neo4j_ref_max_depth
        depth = max(1, min(int(depth), 5))
        if not self.available:
            return {"clause_no": clause_no, "direction": direction, "nodes": [], "edges": [], "available": False}

        pattern = {
            "out": f"(c)-[r:REFERENCES|SUPERSEDES|REFINES*1..{depth}]->(n:Clause)",
            "in": f"(c)<-[r:REFERENCES|SUPERSEDES|REFINES*1..{depth}]-(n:Clause)",
            "both": f"(c)-[r:REFERENCES|SUPERSEDES|REFINES*1..{depth}]-(n:Clause)",
        }.get(direction, f"(c)-[r*1..{depth}]-(n:Clause)")

        try:
            records = self._run(
                f"""
                MATCH (c:Clause {{clause_no: $clause_no}})
                OPTIONAL MATCH {pattern}
                RETURN c.clause_no AS root,
                       collect(DISTINCT {{
                           clause_no: n.clause_no,
                           spec_code: n.spec_code,
                           relations: [rel IN r | type(rel)]
                       }})[0..$limit] AS nodes
                """,
                clause_no=clause_no,
                limit=int(limit),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("引用链查询失败", extra={"clause_no": clause_no, "error": str(exc)[:160]})
            return {"clause_no": clause_no, "direction": direction, "nodes": [], "edges": []}

        if not records:
            return {"clause_no": clause_no, "direction": direction, "nodes": [], "edges": []}
        raw_nodes = records[0].get("nodes") or []
        nodes = [n for n in raw_nodes if n and n.get("clause_no")]
        edges = [
            {"source": clause_no, "target": n["clause_no"], "relations": n.get("relations") or []}
            for n in nodes
        ]
        return {
            "clause_no": clause_no,
            "direction": direction,
            "depth": depth,
            "nodes": nodes,
            "edges": edges,
            "available": True,
        }

    def clause_exists(self, clause_no: Optional[str], spec_code: Optional[str] = None) -> bool:
        """条款存在性校验（供 Judge 识别幻觉引用，FR-JDG-03）。"""
        if not clause_no or not self.available:
            return False
        cypher = "MATCH (c:Clause {clause_no: $clause_no})"
        params: dict[str, Any] = {"clause_no": clause_no}
        if spec_code:
            cypher += " WHERE c.spec_code = $spec_code"
            params["spec_code"] = spec_code
        cypher += " RETURN count(c) AS n"
        try:
            records = self._run(cypher, **params)
            return bool(records and int(records[0].get("n") or 0) > 0)
        except Exception:  # noqa: BLE001
            return False

    def existing_clause_nos(self, clause_nos: Iterable[str]) -> set[str]:
        """批量校验存在的条款号。"""
        nos = [n for n in clause_nos if n]
        if not nos or not self.available:
            return set()
        try:
            records = self._run(
                "MATCH (c:Clause) WHERE c.clause_no IN $nos RETURN DISTINCT c.clause_no AS clause_no",
                nos=nos,
            )
            return {r["clause_no"] for r in records if r.get("clause_no")}
        except Exception:  # noqa: BLE001
            return set()

    def find_conflicts(self, indicator: str, limit: int = 10) -> list[dict[str, Any]]:
        """冲突检测：同一指标在不同规范中要求不一致（FR-KG-06）。"""
        if not self.available or not indicator:
            return []
        try:
            return self._run(
                """
                MATCH (a:Clause)-[:CONFLICTS_WITH]-(b:Clause)
                WHERE a.content CONTAINS $indicator OR b.content CONTAINS $indicator
                RETURN a.clause_no AS clause_a, a.spec_code AS spec_a, a.content AS content_a,
                       b.clause_no AS clause_b, b.spec_code AS spec_b, b.content AS content_b
                LIMIT $limit
                """,
                indicator=indicator,
                limit=int(limit),
            )
        except Exception:  # noqa: BLE001
            return []

    def subgraph(self, clause_nos: Sequence[str], limit: int = 200) -> dict[str, Any]:
        """可视化子图（FR-KG-07）。"""
        if not self.available or not clause_nos:
            return {"nodes": [], "edges": []}
        try:
            records = self._run(
                """
                MATCH (c:Clause) WHERE c.clause_no IN $nos
                OPTIONAL MATCH (c)-[r]-(n)
                RETURN c.clause_no AS source, c.spec_code AS source_spec,
                       type(r) AS relation, labels(n) AS labels,
                       coalesce(n.clause_no, n.text, n.spec_code) AS target
                LIMIT $limit
                """,
                nos=list(clause_nos),
                limit=int(limit),
            )
        except Exception:  # noqa: BLE001
            return {"nodes": [], "edges": []}
        nodes: dict[str, dict] = {}
        edges: list[dict] = []
        for rec in records:
            source = rec.get("source")
            if source:
                nodes.setdefault(source, {"id": source, "type": "Clause", "spec_code": rec.get("source_spec")})
            target = rec.get("target")
            if target:
                node_type = (rec.get("labels") or ["Unknown"])[0]
                nodes.setdefault(target, {"id": target, "type": node_type})
                edges.append({"source": source, "target": target, "relation": rec.get("relation")})
        return {"nodes": list(nodes.values()), "edges": edges}

    def stats(self) -> dict[str, Any]:
        if not self.available:
            return {"available": False}
        try:
            rows = self._run(
                """
                MATCH (c:Clause) WITH count(c) AS clauses
                MATCH (s:Spec) WITH clauses, count(s) AS specs
                OPTIONAL MATCH ()-[r:REFERENCES]->() WITH clauses, specs, count(r) AS refs
                RETURN clauses, specs, refs
                """
            )
            data = rows[0] if rows else {}
            return {
                "available": True,
                "clauses": int(data.get("clauses") or 0),
                "specs": int(data.get("specs") or 0),
                "references": int(data.get("refs") or 0),
            }
        except Exception as exc:  # noqa: BLE001
            return {"available": True, "error": str(exc)[:200]}

    def clear(self) -> None:
        """清空图谱（仅测试/重建使用）。"""
        if not self.available:
            return
        self._run("MATCH (n) DETACH DELETE n")


_store: Optional[GraphStore] = None
_store_lock = threading.Lock()


def get_graph_store() -> GraphStore:
    global _store
    with _store_lock:
        if _store is None:
            _store = GraphStore()
        return _store


def set_graph_store(store: Optional[GraphStore]) -> None:
    global _store
    _store = store


__all__ = ["GraphStore", "get_graph_store", "set_graph_store", "ALLOWED_RELATIONS", "CONSTRAINTS"]
