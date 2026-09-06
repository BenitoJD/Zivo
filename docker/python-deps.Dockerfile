# Shared Python dependency layer for every backend-deps service image
# (migrate, practice, content, study, library, admin, worker).
#
# One venv, built once per requirements.txt change and inherited via
# `ARG PYTHON_DEPS_IMAGE` in each service Dockerfile, so the heavy wheels
# (onnxruntime, litellm, langgraph, pymupdf, trafilatura) are downloaded,
# built, and pushed exactly once per deploy. On the cluster the resulting
# venv layer hash is identical across all 7 service images, so each node
# pulls and stores it once instead of 7 times.
#
# Build from repo root:
#   docker build -f docker/python-deps.Dockerfile -t ghcr.io/benitojd/zivo-python-deps:<tag> .
FROM python:3.12-slim AS deps

COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /build

COPY backend/requirements.txt .

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    # hardlink fallback across filesystems inside BuildKit cache mounts
    UV_LINK_MODE=copy

# The uv cache lives in a BuildKit cache mount, not in the layer: warm
# rebuilds skip the wheel downloads entirely after a runner restart or
# `docker builder prune`.
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install -r requirements.txt \
    && find /opt/venv -depth -type d -name '__pycache__' -exec rm -rf {} + \
    && find /opt/venv -type f -name '*.pyc' -delete \
    && { uv pip uninstall wheel pip || true; }
# This image is pushed only as a build artifact (build tools + uv included);
# service runtimes keep their own clean python:3.12-slim base and COPY the
# venv out of it, which is what makes the venv layer hash identical across
# every service image.
