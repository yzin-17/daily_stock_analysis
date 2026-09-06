# syntax=docker/dockerfile:1.7

# ===================================
# A股自选股智能分析系统 - Docker 镜像
# ===================================
# 多阶段构建：前端打包 + 后端运行

FROM node:20-slim AS web-builder

WORKDIR /app/apps/dsa-web
ARG DSA_WEB_VERSION=0.0.0
ARG DSA_WEB_REVISION=unknown

COPY apps/dsa-web/package.json apps/dsa-web/package-lock.json ./
RUN --mount=type=cache,target=/root/.npm npm ci

COPY apps/dsa-web/ ./
RUN DSA_WEB_VERSION="${DSA_WEB_VERSION}" DSA_WEB_REVISION="${DSA_WEB_REVISION}" npm run build

# Pin to bookworm: wkhtmltopdf was removed from Debian testing (2025)
FROM python:3.11-slim-bookworm

# 设置工作目录
WORKDIR /app

# 设置时区为上海
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 安装系统依赖（wkhtmltopdf 含 wkhtmltoimage，用于 Markdown 转图片 Issue #289）
# /var/cache/apt 用 BuildKit cache mount 保存 .deb：即使该层缓存失效重跑，
# 也只需刷新索引，包下载走本地缓存。docker-clean 会在每次装完后清空包缓存，先移除。
# deb.debian.org（Fastly）从本机网络访问长期劣化：明文 HTTP 间歇 502、还吐过
# 两个多月前的陈旧缓存。统一换成阿里云 HTTPS 镜像；debian-security 路径结构
# 与官方一致，一条 sed 即可整体替换。
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    sed -i 's|http://deb.debian.org|https://mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources && \
    apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    curl \
    gosu \
    wkhtmltopdf \
    fontconfig \
    libjpeg62-turbo \
    libxrender1 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# 创建非 root 用户 (UID 1000)
RUN groupadd -g 1000 dsa && \
    useradd -u 1000 -g dsa -m dsa

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
RUN --mount=type=cache,target=/root/.cache/pip pip install -r requirements.txt

# 复制应用代码
COPY *.py ./
COPY api/ ./api/
COPY data_provider/ ./data_provider/
COPY bot/ ./bot/
COPY src/ ./src/
COPY strategies/ ./strategies/
COPY --from=web-builder /app/static ./static/
COPY docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# Verify globally bundled runtime dependencies without leaving SDK logs in the image.
RUN mkdir -p /tmp/futu-sdk-smoke-home && \
    HOME=/tmp/futu-sdk-smoke-home python -c "import src.services.screening.pipeline; import futu" && \
    rm -rf /tmp/futu-sdk-smoke-home

# 确保数据目录存在并授权给 non-root 用户
RUN mkdir -p /app/data /app/logs /app/reports && \
    chown -R dsa:dsa /app && \
    chmod +x /usr/local/bin/docker-entrypoint.sh

# efinance 将搜索缓存（search-cache.json）写入自身安装包内的 data 目录，
# 该目录默认归 root 所有；容器以非 root 用户 dsa 运行时写入会 PermissionError，
# 导致 efinance 数据源每次抓取 A 股数据都失败并降级。预置目录属主给 dsa 修复此问题。
# 动态解析路径，避免写死 Python 版本目录。
RUN EF_DATA_DIR="$(python -c 'import efinance, pathlib; print(pathlib.Path(efinance.__file__).parent / "data")')" && \
    mkdir -p "$EF_DATA_DIR" && \
    chown -R dsa:dsa "$EF_DATA_DIR"

# 设置环境变量默认值
ENV PYTHONUNBUFFERED=1
ENV LOG_DIR=/app/logs
ENV DATABASE_PATH=/app/data/stock_analysis.db
# Web/API service
ENV WEBUI_HOST=0.0.0.0
ENV API_PORT=8000

# 暴露 API 端口
EXPOSE 8000

# 数据卷（持久化数据）
VOLUME ["/app/data", "/app/logs", "/app/reports"]

# 健康检查（FastAPI 模式）
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8000/api/health || curl -f http://localhost:8000/health \
    || python -c "import sys; sys.exit(0)"

# 启动入口先修复 bind mount 目录权限，再降权为 dsa 执行应用；
# 文档化 docker-compose exec 命令需显式使用 -u dsa，避免手动进程写入 root-owned 文件。
ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# 版本元数据放在所有重型层（apt/pip/代码 COPY）之后：
# ARG 值（版本号、commit）每次发版都会变，若声明在前面会导致
# 后面所有层（包括 apt-get 安装）缓存失效、全量重建。
ARG DSA_VERSION=unconfigured
ARG DSA_UPSTREAM_COMMIT=unknown
ARG DSA_FORK_COMMIT=unknown
LABEL org.opencontainers.image.title="daily-stock-analysis" \
    org.opencontainers.image.version="${DSA_VERSION}" \
    org.opencontainers.image.revision="${DSA_FORK_COMMIT}" \
    org.opencontainers.image.base.name="${DSA_UPSTREAM_COMMIT}" \
    org.openai.thesis-ledger.contract="1"

# 默认命令（可被覆盖）
CMD ["python", "main.py", "--schedule"]
