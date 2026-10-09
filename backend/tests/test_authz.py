# -*- coding: utf-8 -*-
"""越权访问的回归测试（项目隔离 / 知识库授权 / 任务归属）。

背景
----
修复前的实际状态：

- ``GET /eval/tasks`` **默认返回全部用户、全部项目的任务**（仅 ``?mine=true`` 才过滤）；
- 按 ID 访问任务/报告/评审的接口**只判断「是否存在」，不判断「是否归你」**；
- 检索接口的 ``kb_ids`` / ``namespace`` 未做授权校验，传参即可查任意知识库；
- ``User.project_ids`` 字段早已存在，但从未用于任何查询过滤。

后果：任何已登录用户拿到 task_id 就能读取其他项目的评估报告，甚至重跑他人任务。

本文件锁住修复结果。分两部分：
1. 纯逻辑单测（不需要数据库）—— 授权判定规则；
2. 接口级测试（需要数据库）—— 真实 HTTP 请求验证越权被拒。
"""
from __future__ import annotations

import pytest

from app.core.authz import (
    assert_kb_query_allowed,
    assert_report_access,
    assert_task_access,
    can_access_kb,
    can_access_task,
    is_admin,
    namespace_ids_of,
    project_ids_of,
    user_id_of,
    visible_kb_filter,
    visible_task_filter,
)
from app.core.errors import ForbiddenError


# --------------------------------------------------------------------------- #
# 测试替身
# --------------------------------------------------------------------------- #
class FakeUser:
    def __init__(self, uid=None, role="engineer", project_ids=None, username=None):
        self.id = uid
        self.role = role
        self.project_ids = project_ids if project_ids is not None else []
        self.username = username or f"user{uid}"


class FakeTask:
    def __init__(self, task_id="t1", user_id=None, project_id=None):
        self.id = task_id
        self.user_id = user_id
        self.project_id = project_id


class FakeKB:
    def __init__(self, kb_id=1, owner_id=None, project_id=None):
        self.id = kb_id
        self.owner_id = owner_id
        self.project_id = project_id


class FakeReport:
    def __init__(self, report_id="r1", task_id="t1"):
        self.id = report_id
        self.task_id = task_id


class FakeSession:
    """只支持 get(EvalTask, id) 的最小会话替身。"""

    def __init__(self, tasks=None):
        self._tasks = tasks or {}

    def get(self, model, key):
        return self._tasks.get(str(key))


# --------------------------------------------------------------------------- #
# 1) 授权判定规则
# --------------------------------------------------------------------------- #
def test_is_admin_recognizes_admin_role():
    assert is_admin(FakeUser(role="admin")) is True
    assert is_admin(FakeUser(role="ADMIN")) is True
    assert is_admin(FakeUser(role="engineer")) is False
    assert is_admin(FakeUser(role="viewer")) is False


def test_project_ids_of_tolerates_bad_values():
    """project_ids 是 JSON 列，脏数据不应导致 500。"""
    assert project_ids_of(FakeUser(project_ids=[1, 2])) == {1, 2}
    assert project_ids_of(FakeUser(project_ids=["1", "2"])) == {1, 2}
    assert project_ids_of(FakeUser(project_ids=[1, "x", None])) == {1}
    assert project_ids_of(FakeUser(project_ids=None)) == set()


def test_user_id_of_tolerates_bad_values():
    assert user_id_of(FakeUser(uid=7)) == 7
    assert user_id_of(FakeUser(uid="7")) == 7
    assert user_id_of(FakeUser(uid=None)) is None


def test_admin_gets_no_filter():
    """管理员不过滤（返回 None 表示不限）。"""
    assert visible_task_filter(FakeUser(role="admin")) is None
    assert visible_kb_filter(FakeUser(role="admin")) is None


def test_non_admin_gets_restrictive_filter():
    """非管理员必须拿到过滤条件，绝不能是 None（None 等于放开全部）。"""
    assert visible_task_filter(FakeUser(uid=1)) is not None
    assert visible_kb_filter(FakeUser(uid=1)) is not None


def test_filter_is_not_none_for_user_without_projects():
    """没有授权项目的用户也要被限制，不能退化成「不过滤」。"""
    user = FakeUser(uid=5, project_ids=[])
    condition = visible_task_filter(user)
    assert condition is not None, "无项目用户不应拿到 None（那会导致全量可见）"

    # 该用户只能看到自己的任务
    assert can_access_task(user, FakeTask(user_id=5)) is True
    assert can_access_task(user, FakeTask(user_id=6)) is False


def test_can_access_task_by_project_membership():
    user = FakeUser(uid=1, project_ids=[10])
    assert can_access_task(user, FakeTask(user_id=1, project_id=10)) is True   # 自己的
    assert can_access_task(user, FakeTask(user_id=2, project_id=10)) is True   # 同项目
    assert can_access_task(user, FakeTask(user_id=2, project_id=20)) is False  # 别的项目
    assert can_access_task(user, FakeTask(user_id=2, project_id=None)) is False


def test_assert_task_access_raises_forbidden():
    user = FakeUser(uid=1, project_ids=[10])
    assert_task_access(user, FakeTask(user_id=1, project_id=10))  # 不抛
    with pytest.raises(ForbiddenError):
        assert_task_access(user, FakeTask(user_id=2, project_id=20))


def test_admin_can_access_any_task():
    admin = FakeUser(uid=99, role="admin")
    assert can_access_task(admin, FakeTask(user_id=2, project_id=20)) is True


def test_can_access_kb_rules():
    user = FakeUser(uid=1, project_ids=[10])
    assert can_access_kb(user, FakeKB(owner_id=1)) is True             # 自己建的
    assert can_access_kb(user, FakeKB(owner_id=2, project_id=10)) is True   # 本项目
    assert can_access_kb(user, FakeKB(owner_id=2, project_id=20)) is False  # 别的项目


def test_public_kb_visible_to_everyone():
    """未限定项目与创建者的知识库视为「公共库」（如国家现行标准库）。

    这是一条重要语义：若把「project_id 为空」当作无权限，
    种子数据里的 KB-NATIONAL 会让所有非管理员用户查不到任何规范。
    """
    user = FakeUser(uid=1, project_ids=[])
    assert can_access_kb(user, FakeKB(project_id=None, owner_id=None)) is True

    # 但一旦限定了项目，就必须是该项目成员
    assert can_access_kb(user, FakeKB(project_id=20, owner_id=None)) is False


def test_visible_kb_filter_includes_public_kbs():
    """过滤条件必须包含「project_id 为空」，否则公共库会被过滤掉。"""
    user = FakeUser(uid=1, project_ids=[10])
    condition = visible_kb_filter(user)
    assert condition is not None
    # 条件文本里应出现 IS NULL（公共库分支）
    assert "IS NULL" in str(condition), f"过滤条件缺少公共库分支：{condition}"


def test_assert_report_access_follows_task():
    """报告没有独立归属，可见性由所属任务决定。"""
    user = FakeUser(uid=1, project_ids=[10])
    session = FakeSession({"t1": FakeTask("t1", user_id=1, project_id=10),
                           "t2": FakeTask("t2", user_id=2, project_id=20)})
    assert_report_access(user, session, FakeReport(task_id="t1"))  # 不抛
    with pytest.raises(ForbiddenError):
        assert_report_access(user, session, FakeReport(task_id="t2"))
    # 任务不存在也不放行
    with pytest.raises(ForbiddenError):
        assert_report_access(user, session, FakeReport(task_id="missing"))


def test_namespace_ids_parsing():
    assert namespace_ids_of(["kb_3"]) == {3}
    assert namespace_ids_of(["3"]) == {3}
    assert namespace_ids_of(["kb_1", "kb_2"]) == {1, 2}
    assert namespace_ids_of(["default", ""]) == set()


# --------------------------------------------------------------------------- #
# 2) 知识库查询范围收敛（核心修复点）
# --------------------------------------------------------------------------- #
class FakeScalarResult:
    """模拟 SQLAlchemy 的 Result.scalars().all() 链式调用。"""

    def __init__(self, values):
        self._values = values

    def scalars(self):
        return self

    def all(self):
        return list(self._values)


class FakeQuerySession:
    def __init__(self, allowed_ids):
        self._allowed = allowed_ids
        self.executed = 0

    def execute(self, _stmt):
        self.executed += 1
        # accessible_kb_ids 只选取 id 列
        return FakeScalarResult(self._allowed)


def test_admin_query_range_is_unrestricted():
    session = FakeQuerySession([1, 2, 3])
    admin = FakeUser(uid=9, role="admin")
    assert assert_kb_query_allowed(session, admin) is None
    assert assert_kb_query_allowed(session, admin, requested_kb_ids=[7, 8]) == [7, 8]


def test_user_cannot_request_unauthorized_kb():
    """越权请求知识库必须 403 —— 这是修复前「传参即可查任意库」的直接阻断点。"""
    session = FakeQuerySession([1, 2])
    user = FakeUser(uid=1, project_ids=[10])
    with pytest.raises(ForbiddenError):
        assert_kb_query_allowed(session, user, requested_kb_ids=[1, 99])
    # 授权范围内的请求正常放行
    assert assert_kb_query_allowed(session, user, requested_kb_ids=[2]) == [2]


def test_user_without_explicit_kb_ids_is_restricted_to_allowed():
    """未指定 kb_ids 时必须收敛到授权范围，而不是「全部知识库」。"""
    session = FakeQuerySession([3])
    user = FakeUser(uid=1, project_ids=[10])
    assert assert_kb_query_allowed(session, user) == [3]


def test_user_cannot_access_unauthorized_namespace():
    """namespace 决定加载哪个索引分片，同样必须校验。"""
    session = FakeQuerySession([1])
    user = FakeUser(uid=1, project_ids=[10])
    with pytest.raises(ForbiddenError):
        assert_kb_query_allowed(session, user, namespace="kb_99")
    # 授权分片放行
    assert assert_kb_query_allowed(session, user, namespace="kb_1") == [1]


def test_user_with_no_kb_visible_gets_empty_scope():
    """无任何可访问知识库时返回空列表（而不是 None = 不限）。"""
    session = FakeQuerySession([])
    user = FakeUser(uid=1, project_ids=[10])
    assert assert_kb_query_allowed(session, user) == []
