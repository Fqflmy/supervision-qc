# -*- coding: utf-8 -*-
"""知识库与文档管理接口（FR-KB / FR-KG，SRS 6.2 API-02~09）。"""
from __future__ import annotations

from typing import Annotated, Optional

from fastapi import APIRouter, Body, File, Form, Query, Request, UploadFile
from sqlalchemy import func, or_, select

from app.api.deps import CurrentUser, DbSession, client_ip, require_permission
from app.config import settings
from app.constants import AuditAction, ChunkStatus, DocStatus
from app.core.errors import ConflictError, ForbiddenError, NotFoundError, ParamInvalidError
from app.core.logging_conf import get_logger
from app.core.response import ok, paginate
from app.db import add_audit_log
from app.db.models import DocChunk, DocVersion, KnowledgeBase, SpecDoc, User
from app.ingest.splitter import split_blocks
from app.schemas.api import (
    ChunkOut,
    ChunkUpdate,
    DocDetailOut,
    DocVersionOut,
    KgExtractRequest,
    KnowledgeBaseCreate,
    KnowledgeBaseOut,
    ParseRequest,
    PublishRequest,
    ReferenceChainResponse,
    SpecDocOut,
    SpecDocUpdate,
)
from app.services.ingest import ingest_document, namespace_of
from app.services.storage import get_storage

router = APIRouter(tags=["知识库"])
logger = get_logger(__name__)


# --------------------------------------------------------------------------- #
# 知识库（空间）
# --------------------------------------------------------------------------- #
@router.get("/kb", summary="知识库列表")
def list_kbs(session: DbSession, user: CurrentUser) -> dict:
    rows = session.execute(select(KnowledgeBase).order_by(KnowledgeBase.id)).scalars().all()
    return ok([KnowledgeBaseOut.model_validate(r).model_dump() for r in rows])


@router.post("/kb", summary="创建知识库")
def create_kb(
    session: DbSession,
    user: Annotated[User, require_permission("kb:write")],
    body: KnowledgeBaseCreate = Body(...),
) -> dict:
    exists = session.execute(
        select(KnowledgeBase).where(KnowledgeBase.code == body.code)
    ).scalars().first()
    if exists is not None:
        raise ConflictError(f"知识库编码已存在：{body.code}")
    kb = KnowledgeBase(**body.model_dump())
    session.add(kb)
    session.flush()
    add_audit_log(session, AuditAction.CONFIG_CHANGE.value, object_type="knowledge_base", object_id=kb.id)
    session.commit()
    return ok(KnowledgeBaseOut.model_validate(kb).model_dump())


# --------------------------------------------------------------------------- #
# 文档（API-02 ~ API-07）
# --------------------------------------------------------------------------- #
@router.get("/kb/documents", summary="文档列表（按专业/状态/关键字筛选）")
def list_documents(
    session: DbSession,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    specialty: Optional[str] = None,
    status: Optional[str] = None,
    keyword: Optional[str] = None,
    kb_id: Optional[int] = None,
) -> dict:
    stmt = select(SpecDoc)
    if specialty:
        stmt = stmt.where(SpecDoc.specialty == specialty)
    if status:
        stmt = stmt.where(SpecDoc.status == status)
    if kb_id:
        stmt = stmt.where(SpecDoc.kb_id == kb_id)
    if keyword:
        like = f"%{keyword}%"
        stmt = stmt.where(
            or_(SpecDoc.spec_code.ilike(like), SpecDoc.spec_name.ilike(like))
        )
    stmt = stmt.order_by(SpecDoc.id.desc())

    page = max(1, page)
    page_size = min(100, max(1, page_size))
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int(session.execute(count_stmt).scalar() or 0)
    rows = session.execute(stmt.limit(page_size).offset((page - 1) * page_size)).scalars().all()
    items = [SpecDocOut.model_validate(r).model_dump() for r in rows]
    return ok(paginate(items, total, page, page_size))


@router.post("/kb/documents", summary="上传规范文档（支持批量）")
async def upload_document(
    request: Request,
    session: DbSession,
    user: Annotated[User, require_permission("kb:write")],
    files: Annotated[list[UploadFile], File(description="PDF/Word/TXT/Excel")],
    spec_code: Annotated[Optional[str], Form()] = None,
    spec_name: Annotated[Optional[str], Form()] = None,
    specialty: Annotated[Optional[str], Form()] = None,
    region_level: Annotated[str, Form()] = "national",
    issuer: Annotated[Optional[str], Form()] = None,
    kb_id: Annotated[Optional[int], Form()] = None,
) -> dict:
    if not files:
        raise ParamInvalidError("未提供文件")
    if len(files) > settings.upload_max_batch:
        raise ParamInvalidError(f"单批最多上传 {settings.upload_max_batch} 个文件")

    storage = get_storage()
    results: list[dict] = []
    errors: list[dict] = []

    for upload in files:
        filename = upload.filename or "unnamed"
        suffix = ("." + filename.rsplit(".", 1)[-1].lower()) if "." in filename else ""
        if suffix not in settings.upload_allowed_ext_list:
            errors.append({"file": filename, "error": f"不支持的文件类型：{suffix or '无扩展名'}"})
            continue
        code = (spec_code or filename.rsplit(".", 1)[0]).strip()
        if not code:
            errors.append({"file": filename, "error": "无法确定规范编号，请显式提供 spec_code"})
            continue
        existing = session.execute(
            select(SpecDoc).where(SpecDoc.spec_code == code)
        ).scalars().first()
        try:
            relative, digest, size = storage.save(upload.file, filename, subdir="uploads")
        except Exception as exc:  # noqa: BLE001
            errors.append({"file": filename, "error": f"文件保存失败：{str(exc)[:200]}"})
            continue
        if size > settings.upload_max_file_mb * 1024 * 1024:
            storage.delete(relative)
            errors.append({"file": filename, "error": f"文件超过 {settings.upload_max_file_mb}MB 限制"})
            continue

        if existing is None:
            doc = SpecDoc(
                kb_id=kb_id,
                spec_code=code,
                spec_name=spec_name or code,
                specialty=specialty,
                region_level=region_level,
                issuer=issuer,
                status=DocStatus.DRAFT.value,
                uploader_id=getattr(user, "id", None),
            )
            session.add(doc)
            session.flush()
        else:
            doc = existing
            if doc.status == DocStatus.PUBLISHED.value:
                errors.append({"file": filename, "error": "该规范已发布，请先下线再上传新版本"})
                storage.delete(relative)
                continue
            session.query(DocVersion).filter(DocVersion.doc_id == doc.id).update(
                {DocVersion.is_current: False}
            )

        version = DocVersion(
            doc_id=doc.id,
            version_label=filename.rsplit(".", 1)[0][-32:],
            file_name=filename,
            file_path=relative,
            file_size=size,
            file_hash=digest,
            file_type=suffix.lstrip("."),
            parse_status="pending",
            is_current=True,
        )
        session.add(version)
        session.flush()
        add_audit_log(
            session, AuditAction.DOC_UPLOAD.value,
            user_id=getattr(user, "id", None), username=getattr(user, "username", None),
            object_type="spec_doc", object_id=doc.id, ip=client_ip(request),
            detail={"file": filename, "size": size},
        )
        results.append(
            {
                "doc_id": doc.id,
                "version_id": version.id,
                "spec_code": doc.spec_code,
                "file_name": filename,
                "size": size,
                "parse_status": version.parse_status,
            }
        )
    session.commit()
    return ok({"created": results, "errors": errors})


@router.get("/kb/documents/{doc_id}", summary="文档详情")
def get_document(doc_id: int, session: DbSession, user: CurrentUser) -> dict:
    doc = session.get(SpecDoc, doc_id)
    if doc is None:
        raise NotFoundError(f"文档不存在：{doc_id}")
    versions = session.execute(
        select(DocVersion).where(DocVersion.doc_id == doc_id).order_by(DocVersion.id.desc())
    ).scalars().all()
    payload = DocDetailOut(
        **SpecDocOut.model_validate(doc).model_dump(),
        versions=[DocVersionOut.model_validate(v) for v in versions],
    )
    return ok(payload.model_dump())


@router.patch("/kb/documents/{doc_id}", summary="更新文档元数据")
def update_document(
    doc_id: int,
    session: DbSession,
    user: Annotated[User, require_permission("kb:write")],
    body: SpecDocUpdate = Body(...),
) -> dict:
    doc = session.get(SpecDoc, doc_id)
    if doc is None:
        raise NotFoundError(f"文档不存在：{doc_id}")
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(doc, key, value)
    session.commit()
    return ok(SpecDocOut.model_validate(doc).model_dump())


@router.post("/kb/documents/{doc_id}/parse", summary="触发解析、清洗、切分、向量化")
async def parse_document_api(
    request: Request,
    doc_id: int,
    session: DbSession,
    user: Annotated[User, require_permission("kb:write")],
    body: ParseRequest = Body(default=ParseRequest()),
) -> dict:
    doc = session.get(SpecDoc, doc_id)
    if doc is None:
        raise NotFoundError(f"文档不存在：{doc_id}")
    version = session.execute(
        select(DocVersion)
        .where(DocVersion.doc_id == doc_id, DocVersion.is_current.is_(True))
        .order_by(DocVersion.id.desc())
    ).scalars().first()
    if version is None:
        raise NotFoundError("该文档没有可解析的版本")
    if version.parse_status == "parsing":
        raise ConflictError("该版本正在解析中，请稍后")

    doc.status = DocStatus.PARSING.value
    session.flush()
    result = await ingest_document(session, version, doc, force=body.force)
    add_audit_log(
        session, AuditAction.DOC_PARSE.value,
        user_id=getattr(user, "id", None), username=getattr(user, "username", None),
        object_type="spec_doc", object_id=doc_id, ip=client_ip(request),
        detail=result.to_dict(),
    )

    kg_result = None
    if body.build_kg:
        from app.kg.extractor import build_graph_for_doc, link_intra_doc_clauses
        from app.kg.graph_store import get_graph_store

        get_graph_store().init_schema()
        kg_result = await build_graph_for_doc(session, doc_id)
        link_intra_doc_clauses(session, doc_id)
    session.commit()
    return ok({"ingest": result.to_dict(), "knowledge_graph": kg_result})


@router.get("/kb/documents/{doc_id}/chunks", summary="查看切分结果（支持人工修订）")
def list_chunks(
    doc_id: int,
    session: DbSession,
    user: CurrentUser,
    page: int = Query(1, ge=1),
    page_size: int = Query(50, ge=1, le=200),
) -> dict:
    stmt = (
        select(DocChunk)
        .where(DocChunk.doc_id == doc_id, DocChunk.status == ChunkStatus.ACTIVE.value)
        .order_by(DocChunk.chunk_index)
    )
    count_stmt = select(func.count()).select_from(stmt.order_by(None).subquery())
    total = int(session.execute(count_stmt).scalar() or 0)
    rows = session.execute(stmt.limit(page_size).offset((page - 1) * page_size)).scalars().all()
    items = [ChunkOut.model_validate(r).model_dump() for r in rows]
    return ok(paginate(items, total, page, page_size))


@router.patch("/kb/chunks/{chunk_id}", summary="修订单个分块")
def update_chunk(
    chunk_id: int,
    session: DbSession,
    user: Annotated[User, require_permission("kb:write")],
    body: ChunkUpdate = Body(...),
) -> dict:
    chunk = session.get(DocChunk, chunk_id)
    if chunk is None:
        raise NotFoundError(f"分块不存在：{chunk_id}")
    for key, value in body.model_dump(exclude_unset=True).items():
        if value is not None:
            setattr(chunk, key, value)
    # 修订后需要重建该分块索引（FR-KB-10 增量更新）
    from app.retrieval.bm25 import Bm25Document, get_bm25_index
    from app.retrieval.embedding import get_embedder
    from app.retrieval.vector_store import get_faiss_index
    from app.db.models import SpecDoc as _SpecDoc

    doc = session.get(_SpecDoc, chunk.doc_id)
    namespace = namespace_of(doc) if doc is not None else "default"
    get_faiss_index(namespace).remove([chunk_id])
    get_bm25_index(namespace).remove([chunk_id])
    if chunk.status == ChunkStatus.ACTIVE.value:
        vector = get_embedder().encode_one(chunk.content)
        get_faiss_index(namespace).add([vector], [chunk_id], [{"clause_no": chunk.clause_no}])
        get_bm25_index(namespace).add(
            [Bm25Document(chunk_id=chunk_id, text=chunk.content, clause_no=chunk.clause_no, doc_id=chunk.doc_id)]
        )
        get_faiss_index(namespace).save()
        get_bm25_index(namespace).save()
    session.commit()
    return ok(ChunkOut.model_validate(chunk).model_dump())


@router.post("/kb/documents/{doc_id}/publish", summary="审核发布 / 下线 / 废止")
async def publish_document(
    request: Request,
    doc_id: int,
    session: DbSession,
    user: Annotated[User, require_permission("kb:write")],
    body: PublishRequest = Body(...),
) -> dict:
    doc = session.get(SpecDoc, doc_id)
    if doc is None:
        raise NotFoundError(f"文档不存在：{doc_id}")

    if body.action == "publish":
        chunk_count = int(
            session.execute(
                select(func.count(DocChunk.id)).where(DocChunk.doc_id == doc_id)
            ).scalar()
            or 0
        )
        if chunk_count == 0:
            raise ConflictError("该文档尚未解析出任何分块，无法发布")
        doc.status = DocStatus.PUBLISHED.value
    elif body.action == "unpublish":
        doc.status = DocStatus.PENDING_REVIEW.value
    else:
        doc.status = DocStatus.ABOLISHED.value

    add_audit_log(
        session, AuditAction.DOC_PUBLISH.value,
        user_id=getattr(user, "id", None), username=getattr(user, "username", None),
        object_type="spec_doc", object_id=doc_id, ip=client_ip(request),
        detail={"action": body.action, "reason": body.reason},
    )
    session.commit()

    # 发布/下线会改变检索可见性，重建索引保证与数据库一致（FR-KB-08/09）
    from app.services.ingest import rebuild_index

    try:
        stats = await rebuild_index(session)
    except Exception as exc:  # noqa: BLE001
        logger.warning("索引重建失败，稍后可手动重建", extra={"error": str(exc)[:200]})
        stats = {"error": str(exc)[:200]}
    return ok({"doc_id": doc.id, "status": doc.status, "index_rebuild": stats})


# --------------------------------------------------------------------------- #
# 知识图谱（API-08 / API-09）
# --------------------------------------------------------------------------- #
@router.post("/kb/kg/extract", summary="触发实体关系抽取与图谱构建")
async def extract_kg(
    request: Request,
    session: DbSession,
    user: Annotated[User, require_permission("kg:write")],
    body: KgExtractRequest = Body(...),
) -> dict:
    from app.kg.extractor import build_graph_for_doc, link_intra_doc_clauses
    from app.kg.graph_store import get_graph_store

    doc = session.get(SpecDoc, body.doc_id)
    if doc is None:
        raise NotFoundError(f"文档不存在：{body.doc_id}")

    store = get_graph_store()
    store.init_schema()
    result = await build_graph_for_doc(
        session, body.doc_id, limit=body.limit, concurrency=body.concurrency
    )
    linked = link_intra_doc_clauses(session, body.doc_id)
    add_audit_log(
        session, AuditAction.KG_EXTRACT.value,
        user_id=getattr(user, "id", None), username=getattr(user, "username", None),
        object_type="spec_doc", object_id=body.doc_id, ip=client_ip(request), detail=result,
    )
    session.commit()
    return ok(
        {
            "doc_id": body.doc_id,
            "extraction": result,
            "intra_doc_links": linked,
            "graph_available": store.available,
            "graph_stats": store.stats(),
        }
    )


@router.get(
    "/kb/kg/clauses/{clause_no}/refs",
    response_model=None,
    summary="查询条款引用链（正向/反向，深度可配）",
)
def clause_references(
    clause_no: str,
    session: DbSession,
    user: CurrentUser,
    direction: str = Query("both", pattern="^(in|out|both)$"),
    depth: Optional[int] = Query(None, ge=1, le=5),
    include_db: bool = Query(True, description="是否合并 PostgreSQL 中的 clause_ref 冗余索引"),
) -> dict:
    from app.kg.graph_store import get_graph_store

    store = get_graph_store()
    payload = store.reference_chain(clause_no, direction=direction, depth=depth)
    if include_db:
        from app.db.models import ClauseRef

        rows = session.execute(
            select(ClauseRef).where(
                or_(ClauseRef.src_clause_no == clause_no, ClauseRef.dst_clause_no == clause_no)
            )
        ).scalars().all()
        db_edges = [
            {
                "source": r.src_clause_no,
                "target": r.dst_clause_no,
                "relations": [r.relation],
                "confidence": float(r.confidence) if r.confidence is not None else None,
                "source_kind": r.source,
            }
            for r in rows
        ]
        payload.setdefault("edges", [])
        known = {(e.get("source"), e.get("target")) for e in payload["edges"]}
        for edge in db_edges:
            if (edge["source"], edge["target"]) not in known:
                payload["edges"].append(edge)
        payload["db_edges"] = len(db_edges)
    return ok(payload)


@router.get("/kb/kg/stats", summary="图谱统计")
def kg_stats(session: DbSession, user: CurrentUser) -> dict:
    from app.kg.graph_store import get_graph_store

    store = get_graph_store()
    return ok(store.stats())


@router.get("/kb/kg/subgraph", summary="图谱可视化子图")
def kg_subgraph(
    session: DbSession,
    user: CurrentUser,
    clause_nos: str = Query(..., description="逗号分隔的条款号"),
    limit: int = Query(200, ge=1, le=1000),
) -> dict:
    from app.kg.graph_store import get_graph_store

    nos = [n.strip() for n in clause_nos.split(",") if n.strip()]
    return ok(get_graph_store().subgraph(nos, limit=limit))
