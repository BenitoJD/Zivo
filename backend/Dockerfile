FROM python:3.12-slim AS builder

WORKDIR /build

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

COPY requirements.txt .
# Install runtime deps only (dev tools live in requirements-dev.txt, never shipped),
# then slim the venv: strip compiled bytecode (~123 MB) and drop the installer
# toolchain we don't need at runtime (keep setuptools for pkg_resources importers).
# Bytecode strip runs first and unconditionally — it's the big win; pip/wheel
# removal is last + best-effort so pip uninstalling itself can't fail the build.
RUN pip install --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir piper-tts \
    && find /opt/venv -depth -type d -name '__pycache__' -exec rm -rf {} + \
    && find /opt/venv -type f -name '*.pyc' -delete \
    && { pip uninstall -y wheel pip || true; }

FROM python:3.12-slim AS runtime

WORKDIR /app

# ffmpeg (WAV→MP3) + Piper TTS (open-source, on-device, CPU-only). Piper's CLI
# ships in the venv; the voice model is baked into /app/piper_voices so the
# worker renders audio with no network access.
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
    PYTHONDONTWRITEBYTECODE=1

COPY --chown=appuser:appuser . .

# Bake retrieval models so chat does not download from HuggingFace on first request.
# fastembed ignores HF_HOME and caches to its own default (/tmp/fastembed_cache),
# which is exactly where the runtime reads from — so chown THAT path to appuser.
ENV HF_HOME=/app/.cache/huggingface
RUN mkdir -p /app/.cache/huggingface \
    && python -c "from fastembed import TextEmbedding; from fastembed.rerank.cross_encoder import TextCrossEncoder; TextEmbedding(model_name='BAAI/bge-small-en-v1.5'); TextCrossEncoder(model_name='Xenova/ms-marco-MiniLM-L-6-v2')" \
    && chown -R appuser:appuser /app/.cache /tmp/fastembed_cache

USER appuser

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
