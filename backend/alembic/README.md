# 数据库迁移（Alembic）

本目录存放结构迁移脚本。启动时由 `backend/scripts/init_schema.py` 自动执行到最新版本
（入口顺序：等待数据库 → 结构迁移 → 数据种子 → 启动 API）。

---

## 命名约定

```
NNNN_简短英文描述.py
```

- `NNNN`：四位**递增**序号，从 `0001` 起，补零对齐；
- 描述用下划线连接的英文小写，说明这一步做了什么；
- 一个脚本只做一件逻辑上连贯的事（加一列 / 建一张表 / 加一个索引）。

现有脚本：

| 文件 | 内容 |
| --- | --- |
| `0001_baseline_schema.py` | 基线结构 |
| `0002_knowledge_base_ownership.py` | `knowledge_base` 增加 `project_id` / `owner_id`（知识库隔离） |
| `0003_agent_tool_call.py` | 新增 `agent_tool_call`（工具调用审计） |

> ⚠️ 序号需**手工保证唯一**。多人并行开发时容易撞号 ——
> 提交前 `git status` 看一眼有没有别人刚加了同序号的文件。

### 想避免撞号？

可改用 Alembic 默认的时间戳前缀（`alembic revision` 不带 `--rev-id` 时生成
`a1b2c3d4e5f6_xxx.py`），天然不会重复。本仓库选择顺序号是为了让部署人员
一眼看出「一共几步、当前到哪一步」；若后续并行开发频繁，可切换为时间戳方案。

---

## 新增迁移

```bash
cd backend

# 1) 生成骨架（手工填写 upgrade/downgrade）
alembic revision -m "add_xxx_column" --rev-id 0004

# 2) 或从模型自动对比生成（需数据库可连）
alembic revision --autogenerate -m "add_xxx_column"
```

生成后**必须**：

1. 检查 `down_revision` 指向当前最新版本（自动生成通常正确，手工创建要核对）；
2. 填写 `downgrade()`，保证可回滚；
3. **写成幂等**（见下）。

---

## 必须幂等

迁移可能跑在三种库上，其中后两种会让「直接 DDL」失败：

| 库的状态 | 说明 |
| --- | --- |
| 全新库 | 无表，从 `0001` 顺序建 |
| **存量库（未纳管）** | 有表但无 `alembic_version` —— 旧版本用 `create_all` 建的表 |
| 已纳管库 | 正常升级 |

第二种情况下，`create_all` 可能已经建好了目标表/列，直接 `ALTER` / `CREATE`
会报 `DuplicateTable` / `DuplicateColumn`，**导致容器反复重启**。

因此新增迁移时先检查再 DDL：

```python
def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)

    # 加列：先查列是否存在
    columns = {c["name"] for c in inspector.get_columns("some_table")}
    if "new_column" not in columns:
        op.add_column("some_table", sa.Column("new_column", sa.String(64)))

    # 建表：先查表是否存在
    if "new_table" not in inspector.get_table_names():
        op.create_table("new_table", ...)

    # 建索引：先查索引是否存在
    indexes = {i["name"] for i in inspector.get_indexes("some_table")}
    if "ix_some_table_col" not in indexes:
        op.create_index("ix_some_table_col", "some_table", ["col"])
```

`0002` 与 `0003` 都是这么写的，可作范例。

---

## 存量库自动纳管

`app/db/session.py` 的 `migrate_to_latest()` 会检测「有表但无版本记录」的库，
先 `stamp 0001` 标记基线再升级。实测对 23 表的旧库自动恢复成功。

排查结构问题用：

```bash
python scripts/check_schema.py
```

它会判定当前库属于「全新库 / 存量库未纳管 / 已纳管」，并核对关键结构
（`knowledge_base.project_id`、`agent_tool_call` 等）。

---

## 结构落后时不要删卷重建

删卷会**连同评估任务与报告一起丢失**。正确做法是先看 `check_schema.py` 的输出，
再决定是补迁移还是手工修结构。详见运维手册 7.6。

---

## 相关文档

- [部署运维手册 · 5.6 数据库结构迁移](../docs/部署运维手册.md)
- [部署运维手册 · 7.6 故障排查](../docs/部署运维手册.md)
