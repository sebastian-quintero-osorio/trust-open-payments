# API Trust (FastAPI) · imagen de producción.
#
#   docker build -t trust-api .            (desde backend/)
#   docker compose up -d --build           (api + db, ver docker-compose.yml)
#
# Un solo proceso: el worker corre como hilo dentro de la API (00-core "Concurrencia"), así que siempre --workers 1.
FROM python:3.12-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.19 /uv /bin/uv

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    UV_PROJECT_ENVIRONMENT=/app/.venv \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Dependencias primero (capa cacheada mientras no cambie uv.lock); sin el grupo dev.
COPY pyproject.toml uv.lock .python-version ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --locked --no-dev

COPY app ./app

RUN useradd --uid 10001 --user-group --no-create-home --shell /usr/sbin/nologin trust
USER trust

EXPOSE 8000

# 200 = arriba (también "degraded" si op-service no responde); 503 = base de datos caída → unhealthy.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
