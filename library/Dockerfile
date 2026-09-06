# HTTP microservice image: product library (slimmed) + this process's entrypoint.
# Build from repo root: docker build -f library/Dockerfile .
# Builder inherits the shared venv built once by docker/python-deps.Dockerfile;
# the deploy script pins PYTHON_DEPS_IMAGE to the release tag so every service
# of a release shares identical venv layers (built once, pulled once per node).
ARG PYTHON_DEPS_IMAGE="ghcr.io/benitojd/zivo-python-deps:latest"
FROM ${PYTHON_DEPS_IMAGE} AS builder

ENV PATH="/opt/venv/bin:$PATH"

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

COPY backend/app /app/app
COPY backend/scripts/slim_service_tree.py /tmp/slim_service_tree.py
COPY --chown=appuser:appuser library/library_main.py /app/library_main.py
COPY --chown=appuser:appuser library/library_api /app/library_api
RUN python /tmp/slim_service_tree.py library
COPY --chown=appuser:appuser library/alembic.ini /app/alembic.ini
COPY --chown=appuser:appuser library/alembic /app/alembic
COPY --chown=appuser:appuser library/scripts/run_alembic_with_lock.py /app/scripts/run_alembic_with_lock.py
RUN chown -R appuser:appuser /app

USER appuser

EXPOSE 8000

CMD ["uvicorn", "library_main:app", "--host", "0.0.0.0", "--port", "8000"]
