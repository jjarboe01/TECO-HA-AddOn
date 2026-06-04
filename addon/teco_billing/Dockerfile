ARG BUILD_FROM
FROM ${BUILD_FROM}

WORKDIR /app

# Python deps (Chromium already present in the Playwright base image)
COPY app/requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# app code (vendored from sidecar/ + custom_components/teco/ via addon/sync.sh)
COPY app/ ./
RUN chmod +x run.sh

# ingress port (must match config.yaml ingress_port)
EXPOSE 8089

CMD [ "./run.sh" ]
