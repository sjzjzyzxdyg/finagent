# PyFinAgent 应用镜像。数据库(MySQL/pgvector/Redis)由 docker-compose 另起，见 docker-compose.yml。
FROM python:3.11-slim

# 依赖构建可能需要的系统库（psycopg2-binary/akshare/trafilatura 等）
RUN apt-get update && apt-get install -y --no-install-recommends \
        gcc g++ curl \
    && rm -rf /var/lib/apt/lists/*

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 先装依赖，利用镜像层缓存
COPY requirements.txt .
RUN pip install --upgrade pip && pip install -r requirements.txt

# 再拷贝代码
COPY app ./app
COPY sql ./sql
RUN mkdir -p data

EXPOSE 8000

HEALTHCHECK --interval=15s --timeout=5s --start-period=30s --retries=5 \
    CMD curl -fsS http://localhost:8000/health || exit 1

# 生产不加 --reload
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
