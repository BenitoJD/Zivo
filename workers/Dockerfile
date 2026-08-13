# Jobs worker image: ETA lease/handlers + engines they call. No FastAPI routes.
# Build from repo root: docker build -f workers/Dockerfile .
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
    && pip install --no-cache-dir piper-tts \
    && find /opt/venv -depth -type d -name '__pycache__' -exec rm -rf {} + \
    && find /opt/venv -type f -name '*.pyc' -delete \
    && { pip uninstall -y wheel pip || true; }

FROM python:3.12-slim AS runtime

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq5 \
    curl \
    procps \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --create-home --uid 1000 appuser

RUN mkdir -p /app/piper_voices \
    && curl -sL -o /app/piper_voices/en_US-lessac-medium.onnx \
      https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx \
    && curl -sL -o /app/piper_voices/en_US-lessac-medium.onnx.json \
      https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/en/en_US/lessac/medium/en_US-lessac-medium.onnx.json \
    && chown -R appuser:appuser /app/piper_voices

COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONPATH=/app

COPY backend/ /app/
COPY backend/scripts/slim_service_tree.py /tmp/slim_service_tree.py
RUN python /tmp/slim_service_tree.py worker

ENV HF_HOME=/app/.cache/huggingface
RUN mkdir -p /app/.cache/huggingface \
    && python -c "from fastembed import TextEmbedding; from fastembed.rerank.cross_encoder import TextCrossEncoder; TextEmbedding(model_name='BAAI/bge-small-en-v1.5'); TextCrossEncoder(model_name='Xenova/ms-marco-MiniLM-L-6-v2')" \
    && chown -R appuser:appuser /app /tmp/fastembed_cache

USER appuser

CMD ["python", "run_eta_worker_async.py"]
