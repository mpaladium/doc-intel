# UniMERNet formula-recognition sidecar for the ingestion-engine.
#
# Contract (see mineru_server.py and app/pipeline/engines/mineru.py):
#   POST /  body=raw PNG bytes of a formula crop
#   200     {"latex": "..."}      -> equation-lane candidate
#   GET  /                        -> {"ok": true, "model": "..."} health probe
#
# Python 3.11 is deliberate: UniMERNet 0.2.3 pins transformers==4.42.4, which
# will not resolve on 3.13.
FROM python:3.11-slim

ENV PIP_NO_CACHE_DIR=1 \
    PYTHONUNBUFFERED=1 \
    HF_HOME=/models/hf \
    HF_HUB_DISABLE_TELEMETRY=1 \
    HF_HUB_DISABLE_IMPLICIT_TOKEN=1 \
    TRANSFORMERS_NO_ADVISORY_WARNINGS=1 \
    DO_NOT_TRACK=1

RUN apt-get update && apt-get install -y --no-install-recommends \
        libgl1 libglib2.0-0 curl \
    && rm -rf /var/lib/apt/lists/*

# CPU-only torch by default -- swap the index for a CUDA build if you have a GPU
# (see docker-compose.mineru.yml for the deploy.resources.reservations block).
RUN pip install \
        "transformers==4.42.4" \
        "torch" \
        "pillow" \
        "unimernet"

WORKDIR /app
COPY mineru_server.py ./

EXPOSE 8101

HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8101/ >/dev/null || exit 1

ENTRYPOINT ["python", "mineru_server.py", "--host", "0.0.0.0", "--port", "8101"]
