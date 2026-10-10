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

# 带重试的上线。
#
# 为什么必须重试：`docker compose up -d` 在**需要构建镜像**时会把构建一并做掉，
# 而构建依赖外网拉取基础镜像与 pip 包。构建期网络抖动会让命令以非 0 退出，
# 但错误往往是瞬时的（实测遇到过 ReadTimeoutError / subprocess-exited-with-error，
# **隔一次重跑就成功**）。不做重试，用户看到的就是「一键启动失败」。
MAX_ATTEMPTS=3
code=1
for attempt in $(seq 1 "$MAX_ATTEMPTS"); do
  if [[ "$attempt" -gt 1 ]]; then
    warn "第 $((attempt - 1)) 次启动未成功，$((5 * (attempt - 1)))s 后重试（共 ${MAX_ATTEMPTS} 次）…"
    sleep $((5 * (attempt - 1)))
  fi
  if [[ "$BUILD" == "1" ]]; then
    log="logs/compose-up.log"
    [[ "$attempt" -gt 1 ]] && log="logs/compose-up.attempt${attempt}.log"
    docker compose up -d --build >"$log" 2>&1 && code=0 || code=1
  else
    log="logs/compose-up.log"
    [[ "$attempt" -gt 1 ]] && log="logs/compose-up.attempt${attempt}.log"
    docker compose up -d >"$log" 2>&1 && code=0 || code=1
  fi
  [[ "$code" == "0" ]] && break
done

if [[ "$code" != "0" ]]; then
  fail "服务启动失败（已重试 ${MAX_ATTEMPTS} 次），详见 logs/compose-up*.log"
  last="logs/compose-up.attempt${MAX_ATTEMPTS}.log"
  [[ -f "$last" ]] || last="logs/compose-up.log"
  if [[ -f "$last" ]]; then
    hints="$(grep -E 'ReadTimeoutError|Connection timed out|Temporary failure|Could not resolve|no space left|Cannot connect to the Docker daemon|manifest unknown|pull access denied' "$last" | tail -3 || true)"
    if [[ -n "$hints" ]]; then
      printf '  可能的根因：\n'
      printf '%s\n' "$hints" | sed 's/^/    /'
      printf '  → 网络类问题可直接重跑本脚本；磁盘/守护进程类问题需先处理环境。\n'
    fi
  fi
  printf '  排查：docker compose logs --tail 50 api\n'
  exit 1
fi
ok '容器已创建'

# --------------------------------------------------------------------------- #
# 4. 等待健康检查
# --------------------------------------------------------------------------- #
if [[ "$NO_WAIT" != "1" ]]; then
  step '等待服务就绪'
  api_ready=0; web_ready=0
  deadline=$(( $(date +%s) + 360 ))
  # ⚠️ 全栈模式**不映射 api 的 8000 端口**（最小暴露面），只有 web 的 8080 对外。
  # 后端健康检查必须经 nginx 反向代理（location /api/ -> http://api:8000），
  # 否则会一直连不上、等满超时后误报「后端未就绪」，而服务其实是好的。
  # （此前本脚本探的是 127.0.0.1:8000 —— 在 Linux 上必然误报。）
  HEALTH_URL="http://127.0.0.1:${WEB_PORT:-8080}/api/v1/health"
  while [[ $(date +%s) -lt $deadline ]]; do
    if [[ "$api_ready" == "0" ]] && curl -fsS -m 4 "$HEALTH_URL" >/dev/null 2>&1; then
      api_ready=1; ok '后端 API 就绪（经 web 代理 /api）'
    fi
    if [[ "$web_ready" == "0" ]] && curl -fsS -m 4 "http://127.0.0.1:${WEB_PORT:-8080}/" >/dev/null 2>&1; then
      web_ready=1; ok "前端 Web 就绪 :${WEB_PORT:-8080}"
    fi
    [[ "$api_ready" == "1" && "$web_ready" == "1" ]] && break
    sleep 5
  done
  [[ "$api_ready" == "0" ]] && warn "后端 6 分钟内未就绪，请执行 docker compose logs api 排查（探针：$HEALTH_URL）"
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
