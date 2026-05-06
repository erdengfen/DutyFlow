FROM python:3.11-slim

# 安装 uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

WORKDIR /app

ENV PYTHONPATH=/app/src
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
# 让 uv 在容器内直接使用系统 Python，不再创建独立 venv
ENV UV_SYSTEM_PYTHON=1

# 先复制依赖声明文件，利用 Docker 层缓存
COPY pyproject.toml uv.lock ./

# 安装运行时依赖（不安装 dev 依赖）
RUN uv sync --frozen --no-dev

# 复制应用源码和运行时资源
COPY src ./src
COPY skills ./skills
COPY .env.example ./

# 创建本地运行时产物目录占位（实际由卷挂载覆盖）
RUN mkdir -p data/state data/logs data/events data/perception \
    data/contexts data/tasks data/approvals/pending data/approvals/completed \
    data/reports data/plans data/ambient_context

# OAuth callback server 监听端口（/oauth 授权流程使用）
EXPOSE 9768

ENTRYPOINT ["python", "src/dutyflow/app.py"]
