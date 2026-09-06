# Product Alembic migrate image. Not an HTTP process.
# Build: docker build -t zivo-migrate -f backend/Dockerfile --target runtime backend/
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
    PYTHONDONTWRITEBYTECODE=1

COPY --chown=appuser:appuser app /app/app
COPY --chown=appuser:appuser alembic /app/alembic
COPY --chown=appuser:appuser alembic.ini /app/alembic.ini
COPY --chown=appuser:appuser schema /app/schema
COPY --chown=appuser:appuser scripts/run_alembic_with_lock.py /app/scripts/run_alembic_with_lock.py
COPY --chown=appuser:appuser scripts/seed_question_vocab.py /app/scripts/seed_question_vocab.py
COPY scripts/slim_service_tree.py /tmp/slim_service_tree.py
RUN python /tmp/slim_service_tree.py migrate \
    && chown -R appuser:appuser /app

USER appuser

CMD ["python", "scripts/run_alembic_with_lock.py", "--revision", "head"]
