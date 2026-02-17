FROM python:3.12-slim

# 安装系统依赖（meme_generator 等插件需要）
RUN apt-get update && apt-get install -y --no-install-recommends \
    libexpat1 \
    fontconfig \
    libfontconfig1 \
    fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

# 安装 uv 包管理器
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

# 先复制依赖文件，利用 Docker 层缓存
COPY pyproject.toml uv.lock ./

# 安装依赖
RUN uv sync --frozen --no-dev

# 复制项目文件
COPY . .

# NoneBot 默认端口
EXPOSE 8080

CMD ["sh", "-c", "uv run nb orm upgrade && uv run python bot.py"]
