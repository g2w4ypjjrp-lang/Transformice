# syntax=docker/dockerfile:1
# Build context: maliscan/  (docker build -f backend/Dockerfile .)
# Behind a TLS-inspecting proxy, pass its CA: --secret id=ca_bundle,src=/path/ca.crt
FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    MALISCAN_DATABASE_DIR=/app/database
RUN useradd --create-home --uid 10001 maliscan
WORKDIR /app/backend
COPY backend/requirements.txt .
RUN --mount=type=secret,id=ca_bundle,required=false \
    if [ -f /run/secrets/ca_bundle ]; then export PIP_CERT=/run/secrets/ca_bundle; fi; \
    pip install -r requirements.txt
COPY database /app/database
COPY backend/app ./app
COPY backend/scripts ./scripts
RUN mkdir -p /data/storage && chown -R maliscan /data
USER maliscan
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4).status==200 else 1)"
# API by default; the worker/migrate/maintenance services override the command.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "2", "--proxy-headers", "--forwarded-allow-ips", "*"]
