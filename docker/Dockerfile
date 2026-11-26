
# ===================================
# DSA 数据服务镜像
# ===================================
# 多阶段构建保留目标平台的原生 SDK 编译。

# 在目标 glibc/Python 上构建 SDK，避免发行 wheel 的 glibc 版本要求高于运行镜像。
ARG RUST_IMAGE=rust:1.94.1-slim-bookworm
ARG PYTHON_IMAGE=python:3.11-slim-bookworm

# 独立构建时预取固定 SDK 源；本地部署可用同名构建上下文提供已校验源码。
FROM ${PYTHON_IMAGE} AS longbridge_upstream
ARG DEBIAN_MIRROR=https://mirrors.aliyun.com
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=3 update \
    && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
COPY scripts/build_thesis_ledger_longbridge_sdk.py scripts/sdk-source.json /scripts/
RUN python /scripts/build_thesis_ledger_longbridge_sdk.py --fetch-only --work-dir /upstream --output /unused \
    && mv /upstream/source /source

FROM ${RUST_IMAGE} AS sdk-rust
FROM ${PYTHON_IMAGE} AS sdk-builder
ARG DEBIAN_MIRROR=https://mirrors.aliyun.com
COPY --from=sdk-rust /usr/local/cargo /usr/local/cargo
COPY --from=sdk-rust /usr/local/rustup /usr/local/rustup
ENV PATH="/usr/local/cargo/bin:${PATH}" RUSTUP_HOME=/usr/local/rustup
RUN --mount=type=cache,id=dsa-sdk-apt-archives,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=dsa-sdk-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources && \
    apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=45 update && \
    apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=45 install -y --no-install-recommends git gcc g++ pkg-config libssl-dev
WORKDIR /sdk-build
COPY --from=longbridge_upstream /source/ /sdk-upstream/
COPY patches/longbridge-sdk/ ./patches/longbridge-sdk/
COPY scripts/build_thesis_ledger_longbridge_sdk.py scripts/verify_longbridge_native_storage.py scripts/sdk-source.json ./scripts/
RUN --mount=type=cache,target=/sdk-cache \
    CARGO_HOME=/sdk-cache/cargo CARGO_TARGET_DIR=/sdk-cache/target PIP_CACHE_DIR=/sdk-cache/pip \
    python scripts/build_thesis_ledger_longbridge_sdk.py --output /wheels --source-dir /sdk-upstream

# 记录宿主源码输入；数据库结构或 SDK/系统条件变化时快更必须拒绝。
COPY scripts/data-runtime-contract.json scripts/data_runtime_compatibility.py /compat/scripts/
COPY .dockerignore /compat/.dockerignore
COPY requirements.txt /compat/
COPY docker/Dockerfile docker/entrypoint.sh /compat/docker/
COPY patches/longbridge-sdk/ /compat/patches/longbridge-sdk/
COPY scripts/build_thesis_ledger_longbridge_sdk.py scripts/verify_longbridge_native_storage.py scripts/sdk-source.json /compat/scripts/
COPY src/data/fx_rate_repository.py /compat/src/data/
COPY src/services/provider_oauth_store.py src/services/thesis_ledger_control.py /compat/src/services/
RUN python /compat/scripts/data_runtime_compatibility.py --root /compat --output /wheels/data-runtime-inputs.json

# Pin to bookworm: wkhtmltopdf was removed from Debian testing (2025)
FROM ${PYTHON_IMAGE}
ARG DEBIAN_MIRROR=https://mirrors.aliyun.com

# 设置工作目录
WORKDIR /app

# 设置时区为上海
ENV TZ=Asia/Shanghai
RUN ln -snf /usr/share/zoneinfo/$TZ /etc/localtime && echo $TZ > /etc/timezone

# 安装系统依赖（wkhtmltopdf 含 wkhtmltoimage，用于 Markdown 转图片 Issue #289）
# /var/cache/apt 用 BuildKit cache mount 保存 .deb：即使该层缓存失效重跑，
# 也只需刷新索引，包下载走本地缓存。docker-clean 会在每次装完后清空包缓存，先移除。
RUN --mount=type=cache,id=dsa-apt-archives,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,id=dsa-apt-lists,target=/var/lib/apt/lists,sharing=locked \
    rm -f /etc/apt/apt.conf.d/docker-clean && \
    sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources && \
    apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=45 update && \
    apt-get -o Acquire::Retries=3 -o Acquire::https::Timeout=45 install -y --no-install-recommends \
    gcc \
    curl \
    gosu \
    wkhtmltopdf \
    fontconfig \
    libjpeg62-turbo \
    libxrender1 \
    libxext6

# 创建非 root 用户 (UID 1000)
RUN groupadd -g 1000 dsa && \
    useradd -u 1000 -g dsa -m dsa

# 复制依赖文件
COPY requirements.txt .

# 安装 Python 依赖
RUN --mount=type=cache,id=dsa-pip,target=/root/.cache/pip pip install -r requirements.txt

# 基金扩展需显式加载；这里只验证安装与接口，不初始化账号或请求数据。
RUN python -c "import importlib.metadata as m; import rqdatac, rqdatac_fund; assert m.version('rqdatac') == '3.7.1'; assert m.version('rqdatac-fund') == '1.0.44'; assert callable(rqdatac.fund.get_split) and callable(rqdatac.fund.get_dividend)"

COPY --from=sdk-builder /wheels/*.whl /tmp/longbridge-wheels/
COPY --from=sdk-builder /wheels/data-runtime-inputs.json /app/scripts/data-runtime-inputs.json
RUN pip install --no-deps --force-reinstall /tmp/longbridge-wheels/*.whl && \
    python -c "from longbridge.openapi import OAuthBuilder, TradeSessions; assert OAuthBuilder.THESIS_LEDGER_STORAGE_VERSION == 1; assert TradeSessions.Intraday is not None" && \
    rm -rf /tmp/longbridge-wheels

# 复制应用代码
COPY server.py ./
COPY api/ ./api/
COPY data_provider/ ./data_provider/
COPY src/ ./src/
COPY scripts/admit_nav_research.py ./scripts/admit_nav_research.py
COPY scripts/data-runtime-contract.json ./scripts/data-runtime-contract.json
COPY docker/entrypoint.sh /usr/local/bin/docker-entrypoint.sh

# 核对正式数据入口与数据 SDK，临时日志不留在镜像中。
RUN mkdir -p /tmp/dsa-data-smoke && \
    touch /tmp/dsa-data-smoke/empty.env && \
    HOME=/tmp/dsa-data-smoke LOG_DIR=/tmp/dsa-data-smoke/logs ENV_FILE=/tmp/dsa-data-smoke/empty.env \
    DATABASE_PATH=/tmp/dsa-data-smoke/data.db \
    python -c "from server import app; import futu, sys, pathlib; assert app.title == 'DSA 数据服务 API'; assert not any(name in sys.modules for name in ('src.config', 'src.notification', 'src.analyzer', 'src.core.pipeline')); assert all(sys.implementation.cache_tag in path.name for path in pathlib.Path('/app').rglob('*.pyc'))" && \
    rm -rf /tmp/dsa-data-smoke

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
# 数据 API 服务
ENV API_PORT=8000

# 暴露 API 端口
EXPOSE 8000

# 数据卷（持久化数据）
VOLUME ["/app/data", "/app/logs", "/app/reports"]

# 健康检查（FastAPI 模式）
HEALTHCHECK --interval=30s --timeout=10s --start-period=10s --retries=3 \
    CMD curl --fail --silent --show-error http://localhost:8000/health

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
    org.openai.thesis-ledger.contract="1" \
    org.openai.thesis-ledger.data-runtime="dsa-data-runtime-v1"

# 默认命令（可被覆盖）
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
