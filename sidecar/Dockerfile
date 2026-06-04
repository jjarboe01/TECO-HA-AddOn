# TECO auth+data sidecar — uses the official Playwright image (Chromium + OS deps preinstalled).
# Build from the REPO ROOT:  docker build -f sidecar/Dockerfile -t teco-sidecar .
FROM mcr.microsoft.com/playwright/python:v1.43.0-jammy

WORKDIR /app

COPY sidecar/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# vendor the verified parsers/models next to the app (kept DRY with the HA component in source)
COPY sidecar/parsers.py sidecar/models.py sidecar/ibill.py ./
COPY sidecar/teco_auth_sidecar.py sidecar/webui.html ./

ENV HEADLESS=1 \
    SESSION_TTL_MIN=30 \
    PORT=8089
EXPOSE 8089

CMD ["uvicorn", "teco_auth_sidecar:app", "--host", "0.0.0.0", "--port", "8089"]
