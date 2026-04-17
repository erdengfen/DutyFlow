FROM python:3.11-slim

WORKDIR /app

ENV PYTHONPATH=/app/src
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY src ./src
COPY docs ./docs
COPY PLANS.md AGENTS.md ./
COPY .env.example ./

RUN mkdir -p data skills test

ENTRYPOINT ["python", "src/dutyflow/app.py"]
