FROM python:3.12-slim

# 使用国内镜像源（解决中国大陆 apt 连接超时）
RUN sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|deb.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; \
    sed -i 's|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list.d/debian.sources 2>/dev/null || \
    sed -i 's|security.debian.org|mirrors.aliyun.com|g' /etc/apt/sources.list 2>/dev/null; true

# 安装系统依赖（meme_generator 等插件需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 \
    fontconfig \
    libfontconfig1 \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv 包管理器（用 pip+国内镜像，避免慢速拉取 ghcr.io）
RUN pip install --no-cache-dir uv -i https://mirrors.aliyun.com/pypi/simple/

WORKDIR /app

# 先复制依赖文件，利用 Docker 层缓存
COPY pyproject.toml uv.lock ./

# 安装依赖
RUN uv sync --frozen --no-dev

# 复制项目文件
COPY . .

# 与 .env.example 对应：容器内固定路径，不依赖 env_file 是否写全
ENV MEME_HOME=/app/assets/images/meme_generator
ENV LOCALSTORE_PLUGIN_DATA_DIR='{"nonebot_plugin_orm": ".local/orm"}'
ENV PYTHONWARNINGS="ignore::SyntaxWarning"

# NoneBot 默认端口
EXPOSE 8080

CMD ["sh", "-c", "uv run nb orm upgrade && uv run python bot.py"]
