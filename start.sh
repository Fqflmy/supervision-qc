#!/usr/bin/env bash
# 工程监理质量智能评估系统 —— 一键启动脚本（Linux / macOS / Git Bash / WSL）
#
# 用法：
#   ./start.sh                        启动全部服务
#   ./start.sh --api-key sk-xxxx      指定 DeepSeek Key 并启动
#   ./start.sh --with-data            启动并写入示例规范数据
#   ./start.sh --mode infra           只启动数据层（本地开发后端用）
#   ./start.sh --offline              离线演示模式（不调用真实大模型）
#   ./start.sh --build                强制重新构建镜像
#   ./start.sh --down                 停止所有服务
#
# 端口：Web http://localhost:8080   API http://localhost:8000/api/v1/docs

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY="$ROOT/deploy"
ENV_FILE="$DEPLOY/.env"
ENV_EXAMPLE="$DEPLOY/.env.example"

MODE="all"
API_KEY=""
WITH_DATA=0
BUILD=0
DOWN=0
OFFLINE=0
NO_WAIT=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --mode) MODE="${2:-all}"; shift 2 ;;
    --api-key) API_KEY="${2:-}"; shift 2 ;;
    --with-data) WITH_DATA=1; shift ;;
    --build) BUILD=1; shift ;;
    --down) DOWN=1; shift ;;
    --offline) OFFLINE=1; shift ;;
    --no-wait) NO_WAIT=1; shift ;;
    -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "未知参数：$1（用 --help 查看用法）" >&2; exit 2 ;;
  esac
done

step()  { printf '\n\033[36m=== %s ===\033[0m\n' "$1"; }
ok()    { printf '  \033[32m[OK]\033[0m %s\n' "$1"; }
warn()  { printf '  \033[33m[!]\033[0m  %s\n' "$1"; }
fail()  { printf '  \033[31m[X]\033[0m %s\n' "$1"; }

# --------------------------------------------------------------------------- #
# 0. 环境检查
# --------------------------------------------------------------------------- #
step '环境检查'
if ! command -v docker >/dev/null 2>&1; then
  fail '未找到 docker 命令，请先安装 Docker'
  exit 1
fi
ok "docker: $(command -v docker)"

if ! docker version --format '{{.Server.Version}}' >/dev/null 2>&1; then
  fail 'Docker 守护进程未运行，请先启动 Docker'
  exit 1
fi
ok "Docker 引擎: $(docker version --format '{{.Server.Version}}')"

if ! docker compose version --short >/dev/null 2>&1; then
  fail 'docker compose（v2）不可用，请升级 Docker'
  exit 1
fi
ok "Docker Compose: $(docker compose version --short)"

cd "$ROOT"

# --------------------------------------------------------------------------- #
# 停止模式
# --------------------------------------------------------------------------- #
if [[ "$DOWN" == "1" ]]; then
  step '停止服务'
  docker compose down
  docker compose -f "$DEPLOY/docker-compose-infra.yml" -p supervision-infra down >/dev/null 2>&1 || true
  ok '服务已停止（数据卷保留）'
  exit 0
fi

# --------------------------------------------------------------------------- #
# 1. 准备 .env
# --------------------------------------------------------------------------- #
step '准备配置（deploy/.env）'
if [[ ! -f "$ENV_FILE" ]]; then
  if [[ ! -f "$ENV_EXAMPLE" ]]; then
    fail "缺少 $ENV_EXAMPLE，无法生成配置"
    exit 1
  fi
  cp "$ENV_EXAMPLE" "$ENV_FILE"
  ok '已从 .env.example 生成 deploy/.env'
else
  ok 'deploy/.env 已存在，沿用现有配置'
fi

set_env_line() {  # set_env_line KEY VALUE
  local key="$1" value="$2"
  if grep -qE "^[[:space:]]*${key}=" "$ENV_FILE"; then
    # 用 | 作为分隔符，避免 value 中含 / 时出错
    sed -i.bak -E "s|^[[:space:]]*${key}=.*|${key}=${value}|" "$ENV_FILE" && rm -f "${ENV_FILE}.bak"
  else
    printf '%s=%s\n' "$key" "$value" >>"$ENV_FILE"
  fi
}

if [[ -n "$API_KEY" ]]; then
  set_env_line 'DEEPSEEK_API_KEY' "$API_KEY"
  ok '已写入 DeepSeek API Key'
fi

if [[ "$OFFLINE" == "1" ]]; then
  set_env_line 'LLM_PROVIDER' 'fake'
  ok '已切换为离线演示模式（LLM_PROVIDER=fake）'
else
  CURRENT_KEY="$(grep -E '^DEEPSEEK_API_KEY=' "$ENV_FILE" | head -1 | cut -d= -f2- || true)"
  if [[ -z "${CURRENT_KEY// }" ]]; then
    warn 'DEEPSEEK_API_KEY 为空：LLM 相关功能（问答/Agent/Judge）会失败'
    warn '可执行 ./start.sh --api-key sk-xxxx，或编辑 deploy/.env；也可用 --offline 走离线演示'
  else
    ok "DeepSeek API Key 已配置（${CURRENT_KEY:0:7}***）"
  fi
fi

# --------------------------------------------------------------------------- #
# 2. 只启动数据层
# --------------------------------------------------------------------------- #
if [[ "$MODE" == "infra" ]]; then
  step '启动数据层（PostgreSQL + Neo4j + Redis）'
  docker compose -f "$DEPLOY/docker-compose-infra.yml" -p supervision-infra up -d
  ok '数据层已启动：PostgreSQL 5432 / Neo4j 7474,7687 / Redis 6379'
  printf '\n本地开发请接着执行：./scripts/dev.ps1 -Task api 或 python -m app.cli\n'
  exit 0
fi

# --------------------------------------------------------------------------- #
# 3. 构建并启动全部服务
# --------------------------------------------------------------------------- #
step '构建并启动全部服务'
if [[ "$BUILD" == "1" ]]; then
  docker compose up -d --build
else
  docker compose up -d
fi
ok '容器已创建'

# --------------------------------------------------------------------------- #
# 4. 等待健康检查
# --------------------------------------------------------------------------- #
if [[ "$NO_WAIT" != "1" ]]; then
  step '等待服务就绪'
  api_ready=0; web_ready=0
  deadline=$(( $(date +%s) + 360 ))
  while [[ $(date +%s) -lt $deadline ]]; do
    if [[ "$api_ready" == "0" ]] && curl -fsS -m 4 http://127.0.0.1:8000/api/v1/health >/dev/null 2>&1; then
      api_ready=1; ok '后端 API 就绪 :8000'
    fi
    if [[ "$web_ready" == "0" ]] && curl -fsS -m 4 http://127.0.0.1:8080/ >/dev/null 2>&1; then
      web_ready=1; ok '前端 Web 就绪 :8080'
    fi
    [[ "$api_ready" == "1" && "$web_ready" == "1" ]] && break
    sleep 5
  done
  [[ "$api_ready" == "0" ]] && warn '后端 6 分钟内未就绪，请执行 docker compose logs api 排查'
  [[ "$web_ready" == "0" ]] && warn '前端 6 分钟内未就绪，首次构建较慢可稍后刷新页面'
fi

# --------------------------------------------------------------------------- #
# 5. 数据初始化状态（由 api 容器入口自动完成）
# --------------------------------------------------------------------------- #
step '数据初始化检查'
if docker logs supervision-api 2>&1 | grep -qE '\[entrypoint\] 初始化完成|\[DONE\] 本次写入'; then
  ok '示例规范与向量索引已就绪'
else
  warn '初始化仍在进行或未成功，可执行：docker compose exec api python scripts/seed_data.py'
fi

# --------------------------------------------------------------------------- #
# 6. 汇总
# --------------------------------------------------------------------------- #
step '启动完成'
printf '  管理后台    http://localhost:8080\n'
printf '  接口文档    http://localhost:8000/api/v1/docs\n'
printf '  Neo4j 浏览器 http://localhost:7476\n'
printf '  登录账号    admin / Admin@12345\n'
printf '\n  查看状态    docker compose ps\n  查看日志    docker compose logs -f api\n  停止服务    ./start.sh --down\n'
