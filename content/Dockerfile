# HTTP microservice image: product library (slimmed) + this process's entrypoint.
# Build from repo root: docker build -f content/Dockerfile .
FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY backend/requirements.txt .
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && find /opt/venv -depth -type d -name '__pycache__' -exec rm -rf {} + \
    && find /opt/venv -type f -name '*.pyc' -delete \
    && { pip uninstall -y wheel pip || true; }

FROM python:3.12-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY backend/ /app/
COPY backend/scripts/slim_service_tree.py /tmp/slim_service_tree.py
COPY --chown=appuser:appuser content/content_main.py /app/content_main.py
COPY --chown=appuser:appuser content/content_api /app/content_api
RUN python /tmp/slim_service_tree.py content
COPY --chown=appuser:appuser content/alembic.ini /app/alembic.ini
COPY --chown=appuser:appuser content/alembic /app/alembic
COPY --chown=appuser:appuser content/scripts/run_alembic_with_lock.py /app/scripts/run_alembic_with_lock.py
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "content_main:app", "--host", "0.0.0.0", "--port", "8000"]
