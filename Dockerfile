FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

COPY pyproject.toml README.md ./
COPY src ./src
COPY alembic ./alembic
COPY alembic.ini ./alembic.ini

RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -e .

EXPOSE 8080
CMD ["sh","-c","until alembic upgrade head; do echo 'DB not ready; retrying in 3s...'; sleep 3; done; exec uvicorn bayleaf_agents.app:create_app --factory --host 0.0.0.0 --port 8080"]
