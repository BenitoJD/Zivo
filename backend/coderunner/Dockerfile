# Minimal code-execution runner image: Python + C/C++ + Node toolchains, runs as non-root.
FROM python:3.12-slim

RUN apt-get update \
  && apt-get install -y --no-install-recommends gcc g++ libc6-dev nodejs \
  && rm -rf /var/lib/apt/lists/*

RUN useradd -m -u 1000 runner
WORKDIR /app
COPY app.py /app/app.py
USER runner
EXPOSE 2358
CMD ["python3", "/app/app.py"]
