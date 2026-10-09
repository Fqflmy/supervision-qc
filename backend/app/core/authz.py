# -*- coding: utf-8 -*-
"""访问控制：项目隔离、知识库授权、任务归属校验。

设计背景
--------
平台同时服务多个监理项目，每个项目有独立的规范库、评估任务与报告。
修复前的实际状态是：

- ``GET /eval/tasks`` **默认返回全部用户、全部项目的任务**（仅 ``?mine=true`` 才过滤）；
- 按 ID 访问任务/报告/评审的接口**只判断「是否存在」，不判断「是否归你」**；
- 检索接口的 ``kb_ids`` / ``namespace`` 未做授权校验，传参即可查任意知识库；
- ``User.project_ids`` 字段早已存在，但**从未用于任何查询过滤**。

后果：任何已登录用户拿到 task_id 就能读取其他项目的评估报告，
甚至重跑他人的任务；知识库同理。

本模块把授权规则集中到一处，提供两种用法：

1. **查询范围**：``visible_task_filter(user)`` 等返回 SQLAlchemy 条件，
   直接拼进查询，保证「列表」不会越权；
2. **单对象校验**：``assert_task_access(user, task)`` 等，用于按 ID 访问的接口。

管理员（``role == "admin"`` 或拥有 ``*`` 权限）不受限制。
"""
from __future__ import annotations

from typing import Any, Iterable, Optional, Sequence

from sqlalchemy import or_

from app.core.errors import ForbiddenError
from app.core.logging_conf import get_logger

logger = get_logger(__name__)

#: 拥有全部访问权的角色
ADMIN_ROLES = frozenset({"admin"})

#: 项目管理权限（可越权访问本项目内他人数据）
PROJECT_MANAGER_PERMISSIONS = frozenset({"admin:*", "project:manage"})


def _role_of(user: Any) -> str:
    return str(getattr(user, "role", "") or "").strip().lower()


def is_admin(user: Any) -> bool:
    """是否管理员：角色命中，或权限集合含通配符。"""
    if _role_of(user) in ADMIN_ROLES:
        return True
    try:
        from app.api.deps import ROLE_PERMISSIONS

        grants = ROLE_PERMISSIONS.get(_role_of(user), set())
        return "*" in grants or "admin:*" in grants
    except Exception:  # noqa: BLE001 - 依赖注入异常时按最小权限处理
        return False


def project_ids_of(user: Any) -> set[int]:
    """用户被授权访问的项目 ID 集合。"""
    raw: Iterable[Any] = getattr(user, "project_ids", None) or []
    result: set[int] = set()
    for item in raw:
        try:
            result.add(int(item))
        except (TypeError, ValueError):
            continue
    return result


def user_id_of(user: Any) -> Optional[int]:
    value = getattr(user, "id", None)
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# 评估任务
# --------------------------------------------------------------------------- #
def visible_task_filter(user: Any):
    """返回「该用户可见任务」的 SQLAlchemy 条件；管理员返回 None 表示不过滤。"""
    from app.db.models import EvalTask

    if is_admin(user):
        return None

    uid = user_id_of(user)
    project_ids = project_ids_of(user)

    conditions = []
    if uid is not None:
        conditions.append(EvalTask.user_id == uid)
    if project_ids:
        conditions.append(EvalTask.project_id.in_(sorted(project_ids)))

    if not conditions:
        # 既不是管理员、又没有授权项目：只能看到自己发起的任务。
        # 用恒假条件而非返回 None，避免「条件为空 = 不过滤」的越权风险。
        return EvalTask.user_id == uid if uid is not None else EvalTask.id.is_(None)
    return or_(*conditions)


def can_access_task(user: Any, task: Any) -> bool:
    """是否可访问指定任务。"""
    if is_admin(user):
        return True
    uid = user_id_of(user)
    if uid is not None and getattr(task, "user_id", None) == uid:
        return True
    project_id = getattr(task, "project_id", None)
    if project_id is not None and int(project_id) in project_ids_of(user):
        return True
    return False


def assert_task_access(user: Any, task: Any) -> None:
    """校验任务访问权限，不通过则抛 403。

    注意：错误信息**不暴露任务是否存在**的细节差异，
    避免通过「404 还是 403」探测他人任务 ID。
    """
    if not can_access_task(user, task):
        logger.warning(
            "越权访问评估任务被拒绝",
            extra={
                "user_id": user_id_of(user),
                "role": _role_of(user),
                "task_id": str(getattr(task, "id", "")),
                "task_project_id": getattr(task, "project_id", None),
            },
        )
        raise ForbiddenError("无权访问该评估任务")


# --------------------------------------------------------------------------- #
# 评估报告（归属由所属任务决定）
# --------------------------------------------------------------------------- #
def can_access_report(user: Any, session, report: Any) -> bool:
    """报告没有独立的归属字段，其可见性由所属任务决定。"""
    from app.db.models import EvalTask

    if is_admin(user):
        return True
    task_id = getattr(report, "task_id", None)
    if task_id is None:
        return False
    task = session.get(EvalTask, task_id)
    if task is None:
        return False
    return can_access_task(user, task)


def assert_report_access(user: Any, session, report: Any) -> None:
    """校验报告访问权限，不通过则抛 403。"""
    if not can_access_report(user, session, report):
        logger.warning(
            "越权访问评估报告被拒绝",
            extra={
                "user_id": user_id_of(user),
                "role": _role_of(user),
                "report_id": str(getattr(report, "id", "")),
                "task_id": str(getattr(report, "task_id", "")),
            },
        )
        raise ForbiddenError("无权访问该评估报告")


# --------------------------------------------------------------------------- #
# 知识库
# --------------------------------------------------------------------------- #
def visible_kb_filter(user: Any):
    """返回「该用户可见知识库」的 SQLAlchemy 条件；管理员返回 None。

    可见性规则：
    - 公共知识库（``project_id`` 为空）对所有登录用户可见 —— 例如国家现行标准库
      这类不限定项目的规范来源；
    - 项目知识库仅该项目成员（``User.project_ids``）可见；
    - 自己创建的始终可见。

    ⚠️ 不要把「project_id 为空」当成越权口子：它表达的是「不限项目的公共规范」，
    而项目级隔离靠 project_id 有值时的过滤来保证。
    """
    from app.db.models import KnowledgeBase

    if is_admin(user):
        return None

    uid = user_id_of(user)
    project_ids = project_ids_of(user)

    conditions = [KnowledgeBase.project_id.is_(None)]
    if project_ids:
        conditions.append(KnowledgeBase.project_id.in_(sorted(project_ids)))
    if uid is not None:
        # 自己创建的知识库始终可见（例如监理工程师上传的规范）
        conditions.append(KnowledgeBase.owner_id == uid)

    return or_(*conditions)


def accessible_kb_ids(session, user: Any) -> Optional[list[int]]:
    """返回用户可访问的知识库 ID 列表；管理员返回 None 表示不限制。"""
    from sqlalchemy import select

    from app.db.models import KnowledgeBase

    if is_admin(user):
        return None
    condition = visible_kb_filter(user)
    stmt = select(KnowledgeBase.id)
    if condition is not None:
        stmt = stmt.where(condition)
    return [int(row) for row in session.execute(stmt).scalars().all()]


def can_access_kb(user: Any, kb: Any) -> bool:
    """知识库可见性判定（与 visible_kb_filter 保持一致）。"""
    if is_admin(user):
        return True
    project_id = getattr(kb, "project_id", None)
    if project_id is None:
        # 公共知识库：不限定项目，所有登录用户可见
        return True
    uid = user_id_of(user)
    if uid is not None and getattr(kb, "owner_id", None) == uid:
        return True
    return int(project_id) in project_ids_of(user)


def assert_kb_access(user: Any, kb: Any) -> None:
    if not can_access_kb(user, kb):
        logger.warning(
            "越权访问知识库被拒绝",
            extra={
                "user_id": user_id_of(user),
                "role": _role_of(user),
                "kb_id": getattr(kb, "id", None),
                "kb_project_id": getattr(kb, "project_id", None),
            },
        )
        raise ForbiddenError("无权访问该知识库")


def namespace_ids_of(namespaces: Sequence[str]) -> set[int]:
    """把 ``kb_3`` 这类分片名解析成知识库 ID 集合，无法解析的忽略。"""
    result: set[int] = set()
    for name in namespaces or []:
        text = str(name or "").strip()
        if text.startswith("kb_"):
            text = text[3:]
        try:
            result.add(int(text))
        except (TypeError, ValueError):
            continue
    return result


def assert_kb_query_allowed(
    session,
    user: Any,
    *,
    requested_kb_ids: Optional[Sequence[int]] = None,
    namespace: Optional[str] = None,
) -> Optional[list[int]]:
    """校验检索请求的知识库范围，返回**实际允许查询**的 kb_id 列表。

    - 管理员：原样返回请求范围（None 表示不限）；
    - 普通用户：请求范围必须是其授权范围的子集，否则抛 403；
    - 未指定范围时，收敛为用户授权的全部知识库（而不是「全部知识库」）。

    这是修复「传参即可查任意知识库」越权的关键函数。
    """
    allowed = accessible_kb_ids(session, user)
    if allowed is None:  # 管理员
        return list(requested_kb_ids) if requested_kb_ids else None

    allowed_set = set(allowed)

    # namespace 参数同样要收敛（它决定加载哪个 FAISS/BM25 分片）
    if namespace:
        requested_from_ns = namespace_ids_of([namespace])
        if requested_from_ns and not requested_from_ns <= allowed_set:
            logger.warning(
                "越权访问知识库分片被拒绝",
                extra={"user_id": user_id_of(user), "namespace": namespace},
            )
            raise ForbiddenError("无权访问该知识库分片")

    if requested_kb_ids:
        requested = {int(x) for x in requested_kb_ids}
        illegal = requested - allowed_set
        if illegal:
            logger.warning(
                "越权检索知识库被拒绝",
                extra={"user_id": user_id_of(user), "illegal_kb_ids": sorted(illegal)},
            )
            raise ForbiddenError(f"无权访问知识库：{sorted(illegal)}")
        return sorted(requested)

    # 未显式指定 -> 收敛到授权范围（空列表表示「无任何可访问库」）
    return sorted(allowed_set)
