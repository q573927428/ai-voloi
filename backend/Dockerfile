# ==================== 后端生产镜像 ====================
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt pyproject.toml alembic.ini ./
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY migrations ./migrations
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
