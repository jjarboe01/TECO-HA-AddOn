# TECO auth+data sidecar (standalone Docker).
# Slim Python base + Chromium only — installed right after the pip package so
# Playwright and its browser stay in sync.
# Build from the REPO ROOT:  docker build -f sidecar/Dockerfile -t teco-sidecar .
FROM python:3.12-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    HEADLESS=1 \
    SESSION_TTL_MIN=30 \
    PORT=8089

WORKDIR /app

COPY sidecar/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt \
    && playwright install --with-deps chromium

COPY sidecar/parsers.py sidecar/models.py sidecar/ibill.py sidecar/ha_publish.py ./
COPY sidecar/teco_auth_sidecar.py sidecar/webui.html ./

EXPOSE 8089
CMD ["uvicorn", "teco_auth_sidecar:app", "--host", "0.0.0.0", "--port", "8089"]
