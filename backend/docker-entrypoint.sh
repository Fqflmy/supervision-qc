#!/bin/sh
# ---------------------------------------------------------------------------
# 后端容器启动入口（一键启动时自动完成结构迁移与数据初始化）
#
# 流程：等待数据库就绪 → 结构迁移到最新 → 幂等初始化示例数据/索引 → 启动 API 服务。
#
# 为什么「结构迁移」必须排在「数据初始化」之前：
#   seed_data.py 会读写新增字段（例如 knowledge_base.project_id）。
#   早期顺序是 seed → uvicorn，而建表/迁移在 uvicorn 的 lifespan 里才执行，
#   因此对存量库来说种子必然先失败：打印「初始化失败」但服务照常起来，
#   症状表现成「示例数据莫名其妙不见了」，很难定位到是顺序问题。
#
# 为什么把初始化放在 api 容器内做：
#   早期用独立的 compose 服务跑 seed，遇到两个坑：
#   1) `docker compose run` 会改变项目名，导致 seed 与 api 挂载到**不同的卷**，
#      索引写到了 api 看不到的地方（检索恒为空）；
#   2) 需要额外的 depends_on/healthcheck 编排，顺序更难保证。
#   放在 api 容器内执行，天然与 API 共享同一份数据卷与配置。
#
# 结构迁移失败会**阻止启动**（带病启动会出现「代码期望新列、库里没有」的运行期故障，
# 比启动失败更难排查）；数据初始化失败不阻止启动，服务仍可用，只是知识库为空。
# ---------------------------------------------------------------------------
set -eu

echo "[entrypoint] 后端容器启动"

# ---------- 1. 等待 PostgreSQL ----------
if [ -n "${SUPERVISION_DATABASE_URL:-}" ]; then
    echo "[entrypoint] 等待数据库就绪…"
    i=0
    while [ "$i" -lt 60 ]; do
        if python -c "
import os, sys
from sqlalchemy import create_engine, text
try:
    engine = create_engine(os.environ['SUPERVISION_DATABASE_URL'], connect_args={'connect_timeout': 3})
    with engine.connect() as conn:
        conn.execute(text('SELECT 1'))
except Exception as exc:
    sys.exit(1)
sys.exit(0)
" 2>/dev/null; then
            echo "[entrypoint] 数据库就绪"
            break
        fi
        i=$((i + 1))
        sleep 2
    done
    if [ "$i" -ge 60 ]; then
        echo "[entrypoint] 警告：数据库 120 秒内未就绪，仍继续启动（接口会报连接错误）"
    fi
fi

# ---------- 2. 表结构迁移到最新版本（必须在数据初始化之前） ----------
echo "[entrypoint] 检查并升级数据库结构…"
if python scripts/init_schema.py; then
    echo "[entrypoint] 数据库结构已就绪"
else
    echo "[entrypoint] 错误：数据库结构迁移失败，终止启动以避免结构不匹配"
    exit 1
fi

# ---------- 3. 幂等初始化示例数据与索引 ----------
if [ "${SUPERVISION_AUTO_SEED:-true}" != "false" ]; then
    echo "[entrypoint] 初始化示例数据与向量索引（幂等，已有数据会跳过）…"
    if python scripts/seed_data.py; then
        echo "[entrypoint] 初始化完成"
    else
        echo "[entrypoint] 警告：初始化失败，服务仍将启动；可稍后执行 docker compose exec api python scripts/seed_data.py"
    fi
else
    echo "[entrypoint] SUPERVISION_AUTO_SEED=false，跳过初始化"
fi

# ---------- 4. 启动 API ----------
echo "[entrypoint] 启动 API 服务：$*"
exec "$@"
