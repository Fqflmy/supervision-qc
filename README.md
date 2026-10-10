# 工程监理质量智能评估系统

[![CI](https://github.com/Fqflmy/supervision-qc/actions/workflows/ci.yml/badge.svg)](https://github.com/Fqflmy/supervision-qc/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)](https://www.python.org/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.139-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com/)
[![Vue](https://img.shields.io/badge/Vue-3-4FC08D?logo=vuedotjs&logoColor=white)](https://vuejs.org/)
[![License](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

面向工程监理领域的**规范知识库智能检索与自动化质量评估平台**。针对规范文档数量多、人工查询效率低、质量评估依赖专家经验等问题，实现「规范知识管理 → 智能问答 → 标准匹配 → 自动化质量评估」的闭环。

技术栈：**LangChain / LangGraph / DeepSeek(Qwen) / FAISS / Neo4j / PostgreSQL / FastAPI / Vue3 + TypeScript / Docker**

> 需求依据：[工程监理质量智能评估系统-需求规格说明书SRS](docs/工程监理质量智能评估系统-需求规格说明书SRS.md)（2026.01–2026.08）
> 代码中的 `FR-xxx` / `NFR-xxx` 注释均对应 SRS 需求编号。

---

## 1. 架构总览

**前后端完全分离**：后端只提供 REST API（`/api/v1`），前端独立构建为静态资源，两者仅通过 OpenAPI 契约耦合。

```
supervision-qc/
├── backend/                     # 后端服务（FastAPI，Python 3.11+）
│   ├── app/
│   │   ├── config.py            # 全局配置（SUPERVISION_ 前缀环境变量）
│   │   ├── constants.py         # 枚举/状态机迁移表/错误码（对齐 SRS 第 3~4 章）
│   │   ├── core/                # 日志(trace_id)、错误、统一响应、JWT+RBAC
│   │   ├── db/                  # SQLAlchemy 2.0 模型与会话（SRS 5.2 全部表）
│   │   ├── schemas/             # Pydantic 请求/响应契约
│   │   ├── llm/                 # LLM 网关、Token 计数、JSON 容错解析、提示词库
│   │   ├── ingest/              # 文档解析（PDF/Word/TXT/Excel）与条款级切分
│   │   ├── retrieval/           # ★ RAG 多阶段检索管道
│   │   ├── kg/                  # ★ Neo4j 知识图谱与实体关系抽取
│   │   ├── agent/               # ★ LangGraph 五阶段评估 Agent + 状态机 + 收敛守卫
│   │   ├── judge/               # ★ LLM-as-Judge 质量评审 + 引用校验
│   │   ├── services/            # 入库、存储、索引重建
│   │   └── api/                 # 路由、鉴权依赖、审计
│   ├── scripts/                 # 种子数据、模型服务、各类自检脚本
│   ├── tests/                   # pytest（单元 + 集成）
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/                    # 管理后台（Vue3 + TS + Vite + Pinia + Element Plus）
│   ├── src/api/                 # 与 OpenAPI 对齐的类型与 HTTP 客户端
│   ├── src/views/               # 总览/知识库/图谱/问答/评估/评审/系统
│   ├── src/layouts/  src/stores/ src/utils/
│   ├── Dockerfile               # 多阶段构建 → Nginx
│   └── nginx.conf               # SPA 回退 + /api 反向代理
└── deploy/                      # Docker Compose 编排与初始化
    ├── docker-compose.yml       # 全套服务
    ├── docker-compose-infra.yml # 仅数据层（本地开发）
    ├── initdb/                  # PostgreSQL 扩展初始化
    └── .env.example
```

---

## 2. 核心实现说明

### 2.1 RAG 多阶段检索（FR-RET，`app/retrieval/pipeline.py`）

```
查询理解 → Multi-Query 改写 → BM25 + FAISS 双通道并行召回 → RRF 融合
        → 图谱增强扩展 → BGE-Reranker 精排 → 无依据判定 → 上下文组装
```

| 阶段 | 实现要点 |
| --- | --- |
| 查询理解 | 规则+词典：意图分类、专业实体、数值约束、条款号候选（零延迟、无模型依赖） |
| Multi-Query | LLM 生成 3–5 个子查询；**LLM 不可用时自动回退规则模板**，原问题始终置首位 |
| 混合召回 | `jieba + BM25Okapi` 稀疏检索 ∥ FAISS 稠密检索，各 Top-50；**单通道失败自动降级到另一通道** |
| RRF 融合 | `Σ w/(k+rank)`，k=60，通道权重可配（默认 BM25 0.4 / 向量 0.6） |
| 图谱增强 | 命中条款自动扩展上位依据、细化条款与替代版本（FR-KG-05） |
| 精排 | BGE-Reranker-v2-m3 CrossEncoder；无模型时退化为「词面覆盖 + 条款号命中」分数融合 |
| 无依据兜底 | 重排最高分 < 阈值（默认 0.35）即返回 `no_evidence`，**禁止模型自由发挥**（C3 约束） |
| 引用溯源 | 每条结果携带 文件/章节路径/条款号/页码/chunk_id，可定位原文（FR-RET-09） |

### 2.2 知识图谱（FR-KG，`app/kg/`）

- 节点 `Spec/Chapter/Clause/Term/Part/Material/Indicator/Org`，关系 `CONTAINS/REFERENCES/SUPERSEDES/REFINES/CONFLICTS_WITH/APPLIES_TO/DEFINES/ISSUED_BY`；
- LLM 抽取实体关系 → 写入 Neo4j，同时写 `clause_ref` 冗余索引（Neo4j 不可用时检索层仍可追踪引用链）；
- 引用链追踪支持正向/反向、深度可配（默认 3）；
- `clause_exists()` 供 Judge 做**幻觉引用识别**（FR-JDG-03）；
- Neo4j 不可用时 `available=False`，全链路自动跳过图谱阶段而非报错。

### 2.3 评估 Agent（FR-AGT，`app/agent/`）

- **LangGraph 五阶段**：`planning → retrieval → clause_matching → analysis → report_generation`，节点级日志与产物摘要；
- **状态机**：12 个状态、迁移表校验（非法迁移直接拒绝），记录已完成步骤、检索结果、异常状态；
- **防死循环七机制**（SRS 4.3）：

| 机制 | 默认 | 触发动作 |
| --- | --- | --- |
| 最大迭代次数 | 12 | DEGRADED（输出已有结论） |
| 无进展检测 | 连续 2 轮 | NEED_HUMAN |
| 工具调用超时 | 30s | 中断并重试 |
| 单步重试上限 | 3 次（1s/2s/4s 退避） | FAILED |
| 重复调用去重 | 工具+参数指纹 | 复用缓存，**不计数迭代** |
| 总耗时上限 | 900s | NEED_HUMAN |
| Token 预算 | 200K | DEGRADED |

- **检查点持久化**：`langgraph-checkpoint-postgres`（`AsyncPostgresSaver`），检查点表自动创建；服务不可用时降级内存并在返回值与日志中**显式标注**；
- **断点续跑**：恢复时 `iteration_count` / `no_progress_rounds` **沿用不重置**，防止靠反复恢复绕过上限；
- **结论口径确定性化**：总体判定与风险等级由比对结果推导，不采用模型自述，避免结论拔高或淡化。

### 2.4 LLM-as-Judge（FR-JDG，`app/judge/`）

- 五维度打分（各 0–5）+ 加权总分 + 等级；
- **引用校验硬约束**：条款存在性（PostgreSQL + Neo4j 双重确认）、版本有效性、语义一致性；发现幻觉引用直接扣减「条款引用准确性」维度分，防止 Judge 被漂亮文字骗过；
- 双模型交叉评审（DeepSeek ↔ Qwen），维度分歧超阈值标记 `has_conflict`；
- 总分低于阈值（默认 3.5）或存在分歧/幻觉 → `needs_human=True`，任务挂入待人工复核队列。

### 2.5 可观测与可靠性

- **trace_id 贯穿**：中间件生成、响应头回传、结构化 JSON 日志全链路携带；
- **审计日志**：登录/检索/上传/评估/导出/配置变更全量留痕（操作人、IP、对象、结果、trace_id）；
- **降级可见**：Embedding/Reranker/Neo4j/检查点/LLM 任一降级都会在 `/health`、返回值与日志中显式标注，不静默失真；
- **LLM 调用日志**：每次调用记录模型、Token、耗时、是否降级，支撑成本核算。

---

## 3. 快速开始

**已有本仓库**（本地目录）→ 直接看下面的方案 A / B。
**从 GitHub 克隆**：

```bash
git clone https://github.com/Fqflmy/supervision-qc.git
cd supervision-qc
cp deploy/.env.example deploy/.env   # 填入 DEEPSEEK_API_KEY 与 SILICONFLOW_API_KEY
docker compose up -d                 # 一条命令启动全部服务（含自动数据初始化）
```

打开 <http://localhost:8080>，登录 `admin / Admin@12345`。

> 种子数据还会创建 `engineer` / `expert` / `viewer` 三个示例角色账号（密码同上，均已绑定示例项目），便于验证角色权限与项目隔离。

> 无需本地 GPU 或模型权重：检索默认使用硅基流动托管的 BGE-M3 与 BGE-Reranker。
> 没有 API Key 也能跑通全链路：`.\start.ps1 -Offline`（走确定性假模型，用于验证功能）。
> 更简明的日常操作见 [日常使用](docs/日常使用.md)。

提供两条一键启动路径，按场景选择：

| 方案 | 命令 | 适用场景 |
| --- | --- | --- |
| **A. 本机启动**（推荐先试） | 双击 `start-local.bat` | 本机已有 Python/Node；想最快看到界面；Docker Hub 不可达无法构建应用镜像 |
| **B. 全容器启动** | 双击 `start.bat` | 交付/部署，一条命令拉起前后端 + 数据层 + 监控 |

> PowerShell 默认执行策略可能禁止运行 `.ps1`。**双击 `.bat` 包装器即可**（内部用
> `-ExecutionPolicy Bypass`，只对本次调用生效，不改系统策略）；或在终端执行
> `powershell -ExecutionPolicy Bypass -File .\start-local.ps1`。

### 3.1 方案 A：本机一键启动（数据层用 Docker，应用跑本机进程）

```powershell
.\start-local.ps1 -Seed     # 首次：起数据层 + 写示例规范 + 建索引 + 起前后端
.\start-local.ps1           # 之后日常启动
.\start-local.ps1 -Offline  # 无模型 Key 也能跑通全流程（Fake 模型）
.\start-local.ps1 -ApiOnly  # 只起后端
```

脚本自动完成：检查 Python/Node → 启动数据层容器并等端口就绪 →（可选）写入示例数据 →
启动前后端 → 等待健康检查 → 打印访问地址；**Ctrl+C 一次性停止前后端**（数据层保留）。

访问：管理后台 `http://127.0.0.1:5173`，接口文档 `http://127.0.0.1:8000/api/v1/docs`。

### 3.2 方案 B：全容器一键启动（推荐用于交付）

仓库根目录已放好 `docker-compose.yml`，**无需 `-f` 参数**：

```bash
docker compose up -d
```

这一条命令就会：启动 PostgreSQL/Neo4j/Redis/MinIO/监控 → 构建并启动 api 与 web →
**由 api 容器入口自动完成幂等数据初始化**（写示例规范 + 建向量索引）→ 服务就绪。

| 地址 | 说明 |
| --- | --- |
| `http://localhost:8080` | 管理后台（Nginx + 前端静态资源，`/api` 反代到后端） |
| `http://localhost:8000/api/v1/docs` | 接口文档（Swagger UI） |
| `http://localhost:7476` | Neo4j 浏览器 |

常用命令：

```bash
docker compose ps                        # 查看状态
docker compose logs -f api               # 查看后端日志
docker compose up -d --build             # 改代码后重建
docker compose down                      # 停止（数据卷保留）
docker compose down -v                   # 停止并删除数据卷（彻底重置）
```

也可用脚本 `.\start.ps1`（带环境自检、`.env` 准备、健康等待与访问地址汇总）：

```powershell
.\start.ps1 -ApiKey sk-xxxxxxxx   # 指定 Key 启动
.\start.ps1 -WithData             # 显式写入示例数据
.\start.ps1 -Mode infra           # 只起数据层（配合本机后端开发）
.\start.ps1 -Offline              # 离线演示模式
.\start.ps1 -Down                 # 停止全部服务
```

配置文件为 `deploy/.env`（可从 `deploy/.env.example` 复制）。

### 3.3 手动启动（逐步控制，便于排查）

环境：`D:\Miniconda3\envs\test001`（Python 3.12.13）。

```powershell
$py = 'D:\Miniconda3\envs\test001\python.exe'
cd D:\Docment\supervision-qc

# 1) 数据层
docker compose -f deploy\docker-compose-infra.yml -p supervision-infra up -d

# 2) 配置
Copy-Item backend\.env.example backend\.env
#    至少填写：SUPERVISION_LLM_PRIMARY_API_KEY（DeepSeek）
#              SUPERVISION_EMBEDDING_API_KEY / SUPERVISION_RERANKER_API_KEY（硅基流动）

# 3) 写入示例规范并建立索引（幂等）
cd backend
& $py scripts\seed_data.py

# 4) 后端
& $py -m app.cli --host 127.0.0.1 --port 8000

# 5) 前端（另开终端）
cd ..\frontend
npm install
npm run dev
```

> 推荐用 `python -m app.cli` 而非直接 `uvicorn app.main:app`：Windows 上 uvicorn 默认
> 使用 ProactorEventLoop，psycopg 异步不支持它，会让 LangGraph 的 PostgreSQL 检查点
> 降级为内存检查点（重启后无法续跑）。`app.cli` 会在单 worker 场景切换为 SelectorEventLoop。

登录：`admin / Admin@12345`

> **离线演示模式**：未配置模型 Key 时设置 `SUPERVISION_LLM_PROVIDER=fake`，
> 系统用确定性 Fake 网关跑通全流程（用于链路验证与演示，不是真实推理）。

---


## 4. 验证与自检

推荐用统一入口（`scripts/dev.ps1`）：

```powershell
.\scripts\dev.ps1 -Task setup          # 启动数据层 + 写入示例规范 + 建索引
.\scripts\dev.ps1 -Task api-offline    # 离线演示模式启动后端（Fake 模型）
.\scripts\dev.ps1 -Task web            # 启动前端
.\scripts\dev.ps1 -Task verify         # 一键跑完全部自检
.\scripts\dev.ps1 -Task browser-test   # 浏览器端到端（需非受限环境）
```

各脚本与其作用：

| 命令 | 作用 | 实测结果 |
| --- | --- | --- |
| `& $py -m pytest -q` | 单元 + 集成测试 | **97 passed** |
| `& $py scripts\smoke_foundation.py` | 建表、JSON 容错、Fake 网关、bcrypt/JWT | 通过（18 张表） |
| `& $py scripts\smoke_retrieval.py` | 检索管道端到端 | 通过（精确命中 GB 50204-2015 5.3.3） |
| `& $py scripts\smoke_live_embedding.py` | **硅基流动 Embedding/Reranker 连通性与质量** | 通过（语义排序正确、重排命中 0.998） |
| `& $py scripts\smoke_graph.py` | Neo4j 图谱写读、引用链、幻觉识别 | 通过 |
| `& $py scripts\extract_kg_all.py` | **批量知识图谱抽取**（4 份规范） | 通过（实体 427 / 关系 308 / 失败 0 / 125 秒） |
| `& $py scripts\smoke_api.py` | 路由与 OpenAPI 契约校验 | 通过（34 条路径） |
| `& $py scripts\smoke_live_llm.py` | **真实模型连通性**（对话/结构化输出/改写质量） | 通过（DeepSeek） |
| `& $py scripts\smoke_live_api.py` | 真实 HTTP 联调（前端各页面依赖接口） | 通过 |
| `& $py scripts\e2e_check.py --live` | **真实模型全链路端到端** | 通过（五阶段 / 报告 4.8K 字 / Judge 2.9） |
| `& $py scripts\verify_migration_parity.py` | **迁移一致性校验**（迁移 vs create_all 结构、可回退） | 通过 |
| `& $py ../backend/scripts/verify_xss_sanitize.py` | **XSS 净化验证**（真实浏览器投放 14 种载荷） | 通过 |
| `& $py -m pytest tests\test_authz.py` | **授权规则**（项目隔离 / 知识库授权判定） | 通过（19 项） |
| `& $py -m pytest tests\test_isolation_api.py` | **越权回归**（A 项目用户无法读 B 项目任务/报告/规范库） | 通过（12 项） |
| `& $py -m pytest tests\test_agent_tools.py` | **工具调用权限**（工具级授权/参数校验/调用审计） | 通过（21 项） |
| `& $py -m pytest tests\test_user_management_api.py` | **用户与项目授权管理** | 通过（9 项） |
| `& $py -m pytest tests\test_demo_login.py` | **登录身份选择**（生产关闭/角色对应/不可提权） | 通过（6 项） |
| `& $py -m pytest tests\test_role_matrix.py` | **角色权限矩阵**（前后端权限一致 / 各角色菜单契约 / 指标口径） | 通过（6 项） |
| `& $py -m pytest tests\test_schema_bootstrap.py` | **结构初始化与启动顺序**（入口顺序/镜像内容/存量库纳管） | 通过（8 项） |
| `& $py -m pytest tests\test_authz_write_actions.py` | **写操作鉴权**（只读不得创建/执行/裁定；只读可读授权项目内报告） | 通过（10 项） |
| `& $py -m pytest tests\test_human_review.py` | **人工复核裁定与签发**（合格/不合格、不覆盖机器结论、乐观锁、留痕） | 通过（15 项） |
| `& $py scripts\verify_review_e2e.py http://127.0.0.1:8000` | **复核裁定端到端**（含签发、驳回重跑、权限、分歧样本） | 通过（31 项） |
| `& $py scripts\verify_graph_labels.py` | **图谱标签隔离与一致性**（实体不占用保留标签；Spec 数=库中规范数） | 通过（7 项） |
| `& $py scripts\build_reachability_matrix.py` | **角色可达性矩阵**（逐角色实测菜单可见性与页面可达性，无空壳页） | 通过（5 角色 × 9 页面） |
| `& $py scripts\audit_enterprise_ui.py` | **企业化界面**（面包屑导航 + 只读报告详情不泄漏运维字段） | 通过 |
| `& $py scripts\audit_user_crud_ui.py` | **管理员用户管理界面**（增删改查 + 人员身份绑定 + 重置密码全流程） | 通过（29 项） |
| `& $py scripts\check_schema.py` | **结构自查**（判定全新库 / 存量库未纳管 / 已纳管） | 通过 |
| `& $py scripts\shot_roles.py` | **逐角色界面截图**（核验菜单集合与落地页） | 通过（4 角色） |
| `& $py -m pytest tests\test_metrics_endpoint.py` | **指标端点与抓取鉴权**（Prometheus 文本格式） | 通过（20 项） |
| `& $py -m pytest tests\test_tracing.py` | **链路追踪**（优雅关闭/空操作/形状压缩/接入点） | 通过（15 项） |
| `& $py scripts\verify_langsmith.py` | **LangSmith 接入验证**（Key 有效性 + 数据可查回） | 通过 |
| `& $py scripts\verify_langsmith_agent.py` | **全链路追踪验证**（真实任务 + 层级核对） | 通过（18 span） |
| `& $py scripts\verify_isolation_live.py` | **真实账号隔离验证**（对比 admin/engineer/viewer 可见范围） | 通过 |
| `& $py scripts\verify_deploy.py` | **容器化部署功能验收**（健康/鉴权/检索/问答/Agent/报告/Judge/看板/图谱/指标） | **通过 33/33** |
| `& $py scripts\browser_e2e.py --base http://127.0.0.1:8080` | **浏览器端到端**（Edge 驱动 13 个页面 + 截图） | **通过 28/28**（含用户管理页与角色隔离），控制台零错误 |
| `& $py scripts\rebuild_index.py` | 换 Embedding 模型后重建全量索引 | 通过（94 分块 / 15.8 s） |
| `& $py scripts\fix_checkpoint_locks.py --fix` | 检查点索引锁等待的诊断与修复 | 见「常见问题」第 4 条 |
| `npx vue-tsc --noEmit` | 前端类型检查 | 通过 |
| `npm run build` | 前端生产构建 | 通过 |

> `browser_e2e.py` 默认自动拉起前后端；带 `--base` 参数时直接测已部署环境（容器化部署 8080）。
> 它用 Playwright 驱动系统自带 Edge 遍历登录/总览/知识库/规范详情/图谱/问答/评估/任务详情/
> 报告/Judge/用户管理/系统共 13 个页面，产出截图到 `var/artifacts/`。

**实测关键指标**：

| 指标 | 说明 |
| --- | --- |
| 检索热路径延迟 | 2–4 s（真实 Embedding + Reranker 各一次外部调用） |
| 检索命中 | GB 50204-2015 **5.3.3**，重排得分 **0.999**（BGE-M3 + BGE-Reranker） |
| 语义泛化 | 「脚手架那个连着墙的杆子要多远一个」→ JGJ 130-2011 6.4.2，得分 0.908 |
| Multi-Query 改写 | **口语→规范术语**（「太热了」→「水化热温度控制要求」） |
| Agent 全流程 | 5 迭代 / 5 子任务 / **23–28 s** / 14–17K token / 检查点 postgres |
| 条款判定 | 2 项不符合（温度超限、养护不足）+ 3 项证据不足 |
| Judge 评审 | 3.3–4.1 / 5.00，五维齐全，正确给出等级与人工复核建议 |
| 报告篇幅 | 4.4K–5.8K 字（含逐条比对表、整改建议、引用溯源附录） |
| 容器化验收 | 33/33 功能项通过；浏览器端到端 23/23 |

> Judge 评审在 `e2e_check.py` 的自检用例中会因**故意注入的幻觉引用**触发硬约束扣分，
> 这是预期行为；评审意见中仍保留真实引用准确率，便于人工复核判断。

---

## 5. Docker Compose 部署说明

### 5.1 服务清单（SRS 7.7）

| 服务 | 说明 | 端口 |
| --- | --- | --- |
| `web` | Nginx + 前端静态资源，`/api` 反代到 api | **8080** |
| `api` | FastAPI（含启动自检 + **幂等数据初始化入口**） | 内部 8000 |
| `postgres` / `neo4j` / `redis` / `minio` | 数据层 | 内部 |
| `prometheus` / `grafana` | 监控 | 内部 |
| `embedding` / `reranker` | 本地 BGE 推理服务，**默认不启动**（`profiles: ["local-models"]`） | 内部 8510 / 8520 |

默认使用硅基流动托管 Embedding/Reranker，因此不需要本地模型容器与 torch 依赖：

```bash
docker compose --profile local-models up -d    # 需要本地推理时再启用
# 同时把 deploy/.env 的 EMBEDDING_PROVIDER / RERANKER_PROVIDER 改为 http
```

### 5.2 数据初始化如何完成

`api` 容器的入口脚本 `backend/docker-entrypoint.sh` 会依次执行：

1. 等待 PostgreSQL 就绪；
2. 幂等执行 `scripts/seed_data.py`（已有规范则跳过，不会重复写入）；
3. 启动 Uvicorn。

初始化失败**不会阻止服务启动**（知识库为空但接口可用），可手动补救：

```bash
docker compose exec api python scripts/seed_data.py
docker compose exec api python scripts/rebuild_index.py   # 换 Embedding 模型后重建索引
```

> 为什么不做成独立的 `seed` 服务：`docker compose run` 会改变 Compose 项目名，
> 导致 seed 与 api 挂载到**不同的卷**，索引写到 api 看不见的位置（表现为检索恒为空）。
> 放在 api 容器内执行，天然共享同一份数据卷与配置。

### 5.3 两种模式的关系

`deploy/docker-compose-infra.yml`（本机开发用）与全栈编排共用同一套账号/镜像规范，
但**不写 `container_name`**，由 Compose 按项目名自动命名（如 `supervision-infra-postgres-1`），
因此两种模式可以在同一台机器上共存、容器名不冲突。两侧数据卷相互独立。

---


## 6. 部署与运维注意

- **JWT 密钥（强制校验）**：占位密钥曾随公开仓库分发，任何拿到仓库的人都能伪造令牌。
  现已改为：`SUPERVISION_ENVIRONMENT=production` 时，若 `JWT_SECRET` 为空、为占位值或
  长度 < 32，**服务会拒绝启动**（这是预期行为，不是故障）。开发环境留空即可，
  会自动生成随机密钥。生成方式：`python -c "import secrets;print(secrets.token_urlsafe(48))"`；
- **数据库结构变更必须走迁移**：正式环境设置 `SUPERVISION_DB_SCHEMA_MODE=alembic`，
  启动时会自动 `alembic upgrade head`。开发环境默认仍用 `create_all`（它会创建缺失的表，
  但**不处理列变更**）。存量库需先 `alembic stamp head` 纳管；
  详见[部署运维手册 5.6](docs/部署运维手册.md#56-数据库结构迁移alembic)；
- **生产必改**：数据库/Neo4j/MinIO/Grafana 密码、初始管理员密码
  （`Admin@12345` 已公开在文档中）；
- **单 worker 说明**：FAISS/BM25 索引驻留进程内存，`--workers 1` 可避免多进程索引不一致；
  横向扩展需把向量检索切到独立服务（Compose 已预留 `embedding`/`reranker` 形态）；
- **数据目录必须与卷挂载点一致**：配置项 `SUPERVISION_DATA_DIR` 决定索引与上传文件的落盘位置，
  compose 中固定为 `/app/var` 并把命名卷 `app_var` 挂到同一路径。若两者不一致，
  数据会写进容器可写层——重启即丢失，且看起来「明明写入了却检索不到」；
- **长任务连接可能被回收**：一次评估持续数分钟，期间会话持有的连接可能被服务端断开。
  落库阶段已内置「丢弃坏连接 + 重放」自愈。注意恢复顺序必须
  `invalidate()` 先于 `rollback()`，顺序颠倒反而会阻断恢复（详见运维手册 7.2）；
  也不要给检查点连接池设 `statement_timeout`——会误杀长任务，导致
  `checkpoint_backend=memory`；
- **健康检查日志**：`/health` 被容器每 20 秒轮询一次，成功请求不记访问日志（失败仍记录），
  避免淹没业务日志；
- **Windows 事件循环**：psycopg 异步模式不支持 `ProactorEventLoop`，`app/cli.py` 在单 worker
  场景切换为 `SelectorEventLoop`（仅 win32 生效）；
- **模型下载**：内网环境建议把 BGE 权重预置到 `var/models/`，或直接使用硅基流动托管服务。

---


## 7. 已知限制

| 项 | 说明 |
| --- | --- |
| Embedding / Reranker | 默认使用**硅基流动托管服务**（`BAAI/bge-m3` + `BAAI/bge-reranker-v2-m3`），效果与本地权重一致且无需下载。若需完全离线，可把权重放到 `var/models/` 后改 `SUPERVISION_EMBEDDING_PROVIDER=local`；两者都不可用时自动降级为哈希向量 + 分数融合，**不会再长时间挂起**（缺权重时快速降级并告警） |
| 图谱数据 | 已对示例规范完成抽取：**134 个条款节点 / 12 个规范节点 / 2 条跨规范引用**，关系 `DEFINES` 560 条、`BELONGS_TO` 82 条。新增规范后执行 `python scripts/extract_kg_all.py` 增量抽取即可（会调用大模型，94 条款实测 125 秒、失败 0）。`REFERENCES` 边较少属正常：国标条款多以「应符合现行国家标准的规定」概括引用，很少写出具体条款号 |
| OCR 降级链路 | 扫描件依赖 `pytesseract` + `pdf2image`，未安装时安全跳过并告警（`SUPERVISION_PARSER_OCR_ENABLED=true` 开启） |
| 真实模型成本 | 一次完整评估（5 子任务）约消耗 14–17K token；生产建议配置任务级 Token 预算（默认 200K）与并发上限 |
| 单机索引 | FAISS/BM25 驻留单进程内存，`--workers 1`；横向扩展需将向量检索服务化（Compose 已预留形态） |
| 首次启动耗时 | api 容器入口要先等数据库、再写示例数据并调用真实 Embedding 建索引，首次约 1–3 分钟；`docker compose logs -f api` 可观察进度 |

---

## 8. 常见问题

**Q1：填了 `backend\.env` 的 API Key 却不生效？**
确认后端工作目录为 `backend/`，且 `.env` 位于 `backend\.env`（配置根目录由 `app/config.py` 的
`PROJECT_ROOT` 决定，等价于 `backend/`）。`.env` 用 UTF-8 **无 BOM** 保存。
自检：`python -c "from app.config import settings; print(bool(settings.llm_primary_api_key))"`。
注意**环境变量优先级高于 `.env`**：曾经在启动命令里设过 `SUPERVISION_EMBEDDING_PROVIDER=hash`，
导致服务用哈希向量去查真实 BGE 索引，检索分数暴跌到 0.002。排查时先确认没有残留的环境变量。

**Q2：检索返回空 / 找不到条款？**
按顺序检查：
1. `docker compose logs api | grep 入库` 确认示例规范已写入（或手动执行 `seed_data.py`）；
2. `/api/v1/health` 中 `vector_index_default.size` 是否大于 0，`embedding.degraded` 是否为 `false`；
3. 建索引用的 Embedding 必须与查询时一致——换过模型要跑 `scripts/rebuild_index.py`；
4. 文档状态须为 `published`（未发布文档不参与检索）。

**Q3：容器里数据明明写入了却检索不到？**
检查 `SUPERVISION_DATA_DIR` 与卷挂载点是否一致（compose 中为 `/app/var`）。
若卷挂在 `/app/../var` 而应用读 `/app/var`，索引会写进容器可写层，重启即丢。

**Q4：接口全部卡住、Agent 一直不返回？**
典型原因是**检查点索引上的锁等待**：`AsyncPostgresSaver.setup()` 会执行
`CREATE INDEX CONCURRENTLY`，而它必须等所有并发事务结束；若有连接停在
`idle in transaction` 就会互相等死。诊断与修复：

```bash
docker compose exec -T api python /app/scripts/fix_checkpoint_locks.py          # 只诊断
docker compose exec -T api python /app/scripts/fix_checkpoint_locks.py --fix    # 清理并重建索引
```

**Q5：`checkpoint_backend=memory`，重启后无法续跑？**
说明检查点降级了。常见原因：Windows 上直接用了 `uvicorn app.main:app`（Proactor 事件循环）
而非 `python -m app.cli`；或给检查点连接池设了过短的 `statement_timeout`，
使一次长评估中途被数据库取消。日志中搜「降级为内存检查点」可见具体错误。

**Q6：升级后索引位置变了？**
运行时数据统一在 `backend/var/`（faiss 索引、storage 原始文件、seed 样本），
容器内对应该目录的命名卷。历史版本曾放在仓库根 `var/`，
可用 `python scripts/migrate_var.py --apply` 迁移。

---

## 9. 相关文档

| 文档 | 说明 |
| --- | --- |
| `docs/日常使用.md` | **日常使用**：一页搞定启动/停止/重启/常见情况（平时只看这个就够） |
| `docs/概要设计说明书.md` | 概要设计：模块划分、关键时序、图状态机、部署拓扑、需求追踪矩阵 |
| `docs/部署运维手册.md` | **部署与运维**：环境要求、配置项全量说明、启动方式、数据备份恢复、监控告警、故障排查手册、安全加固、上线检查清单、命令速查 |
| `docs/上线流程与就绪度评估.md` | **上线流程**：七阶段上线路径、缺口清单与工作量、安全评审、演练项、灰度与验收、组织合规事项、可勾选检查清单 |
| `docs/角色权限审计与设计说明.md` | **角色权限**：四个角色的职责与能力矩阵、实测发现的问题（含越权与可用性缺口）、修复计划 |
| `docs/角色可达性矩阵.md` | **可达性矩阵**：逐角色实测「从落地页能到达哪些功能」，含隐藏页入口与界面可用性说明 |
| `docs/修改建议-第二轮.md` | **修改建议（现行）**：2 个 P0（工程师发不起评估、复核接口越权）、3 个 P1、5 个 P2，含实施顺序与工作量 |
| `docs/人工复核整改方案.md` | **人工复核整改**：补齐「合格/不合格裁定」与「终审签发」（SRS 要求但实现缺失），含数据模型、接口、界面、迁移与决策点 |
| `docs/评估操作示例.md` | **评估操作示例**：一个从发起到签发、可照做的完整例子（含实测数字与预期结果） |
| [需求规格说明书 SRS](docs/工程监理质量智能评估系统-需求规格说明书SRS.md) | 需求基线（63 条功能需求、状态机、接口清单、验收标准） |
| `README.md`（本文） | 架构说明、快速开始、验证方式、常见问题、已知限制 |
| [LICENSE](LICENSE) | MIT 许可证 |

---

## 10. 贡献与许可

- **许可证**：本项目采用 [MIT License](LICENSE)，可自由使用、修改与分发（保留版权声明）。
- **提交规范**：建议使用 Conventional Commits（`feat:` / `fix:` / `docs:` / `chore:`）。
- **提交前自检**：`python -m pytest -q`（后端）、`npx vue-tsc --noEmit`（前端类型）。
- **CI**：推送后 GitHub Actions 会自动执行后端测试、前端构建与编排校验。
- **密钥**：请勿提交真实 `.env`；仓库已通过 `.gitignore` 排除，只保留 `.env.example` 模板。
