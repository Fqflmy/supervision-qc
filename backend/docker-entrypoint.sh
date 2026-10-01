#!/bin/sh
# ---------------------------------------------------------------------------
# 后端容器启动入口（一键启动时自动完成数据初始化）
#
# 流程：等待数据库就绪 → 幂等初始化示例数据/索引 → 启动 API 服务。
#
# 为什么放在 api 容器内做初始化：
#   早期用独立的 compose 服务跑 seed，遇到两个坑：
#   1) `docker compose run` 会改变项目名，导致 seed 与 api 挂载到**不同的卷**，
#      索引写到了 api 看不到的地方（检索恒为空）；
#   2) 需要额外的 depends_on/healthcheck 编排，顺序更难保证。
#   放在 api 容器内执行，天然与 API 共享同一份数据卷与配置。
#
# 初始化失败不会阻止 API 启动：服务仍可用，只是知识库为空，
# 后续可手动执行 `docker compose exec api python scripts/seed_data.py` 补救。
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

# ---------- 2. 幂等初始化示例数据与索引 ----------
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

# ---------- 3. 启动 API ----------
echo "[entrypoint] 启动 API 服务：$*"
exec "$@"
