# Budgie: one container, FastAPI serving the API and the static frontend (decision 0005).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY backend/pyproject.toml backend/
COPY backend/app backend/app
RUN pip install ./backend

COPY frontend frontend

# Non-root user; data lives only on /data with mode 700 (decision 0006).
RUN useradd --system --uid 10001 --no-create-home budgie \
    && mkdir -p /data/uploads \
    && chown -R budgie /data \
    && chmod 700 /data

ENV DATABASE_URL=sqlite:////data/budgie.db \
    UPLOAD_DIR=/data/uploads \
    FRONTEND_DIR=/app/frontend

USER budgie
VOLUME /data
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health', timeout=4)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
