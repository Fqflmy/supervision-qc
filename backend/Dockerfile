# 后端镜像（前后端分离：本镜像只提供 REST API）
FROM python:3.12-slim

# INSTALL_MODELS=0 时不安装 sentence-transformers/FlagEmbedding：
# api 容器用 http provider 调用独立的 embedding/reranker 服务，无需本地模型依赖，
# 镜像体积与构建时间显著下降；需要本地推理时用 --build-arg INSTALL_MODELS=1。
ARG INSTALL_MODELS=0

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Shanghai \
    PIP_DEFAULT_TIMEOUT=120 \
    PIP_RETRIES=10

WORKDIR /app

# 说明：不安装 apt 系统包。
# - psycopg[binary] 自带 libpq，无需 libpq-dev/build-essential；
# - pdfplumber 为纯 Python，无需 poppler；
# - reportlab 用内置 Adobe CJK CID 字体（STSong-Light）生成中文 PDF，
#   同样无需字体文件与系统库；
# - 仅当启用 OCR（SUPERVISION_PARSER_OCR_ENABLED=true）时才需要 tesseract/poppler，
#   那属于可选能力，需要时自行在此基础上追加 apt 层。
# 这样镜像更小、构建不依赖 Linux 软件源（企业内网/受限网络下更可靠）。

COPY requirements.txt .
# --retries/--timeout 显式传入：默认值（5 次 / 15s）在慢速或跨境网络下
# 会在下载大包（如 reportlab 依赖链）时超时失败，表现为
# `ReadTimeoutError: HTTPSConnectionPool(...) Read timed out`，
# 整个镜像构建中断。这里放宽到 10 次 / 120s，显著提高构建成功率。
RUN if [ "$INSTALL_MODELS" = "1" ]; then \
        pip install --no-cache-dir --retries 10 --timeout 120 -r requirements.txt ; \
    else \
        grep -v -E "^(sentence-transformers|FlagEmbedding)" requirements.txt > /tmp/req-slim.txt ; \
        pip install --no-cache-dir --retries 10 --timeout 120 -r /tmp/req-slim.txt ; \
    fi

COPY app ./app
COPY scripts ./scripts
# alembic.ini 与 alembic/ 迁移脚本必须打进镜像：
# 容器入口会用 `alembic upgrade head` 升级表结构（在数据初始化之前执行），
# 缺失时会报 "Path doesn't exist: /app/alembic" 并导致容器反复重启。
COPY alembic.ini ./
COPY alembic ./alembic
COPY pytest.ini ./

# 启动入口：等待数据库 → 幂等初始化示例数据与索引 → 启动 API
# （放在容器内执行可确保初始化与 API 使用同一份数据卷与配置，见脚本注释）
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh
RUN chmod +x /usr/local/bin/docker-entrypoint.sh \
    && mkdir -p /app/var/storage /app/var/faiss

EXPOSE 8000

# 健康检查不依赖 curl（镜像内未安装），直接用 Python 标准库探测。
# start_period 放宽到 90s：首次启动需等待数据库并写入示例数据与向量索引。
HEALTHCHECK --interval=20s --timeout=8s --retries=6 --start-period=90s \
    CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health',timeout=5)" || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# 生产建议单 worker：FAISS/BM25 索引驻留进程内存，多 worker 会各自持有副本导致结果不一致
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-access-log"]
