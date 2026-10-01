# syntax=docker/dockerfile:1
# Connect app and worker image (FastAPI, uvicorn).
FROM python:3.12.11-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-client osslsigncode curl \
 && rm -rf /var/lib/apt/lists/* \
 && useradd --system --uid 10001 --home /srv/connect connect

WORKDIR /srv/connect
COPY requirements.lock ./
# The optional "extra_ca" secret is only for builders behind a TLS
# intercepting proxy; it is a no-op on a normal install.
RUN --mount=type=secret,id=extra_ca,required=false \
    if [ -s /run/secrets/extra_ca ]; then export PIP_CERT=/run/secrets/extra_ca; fi; \
    pip install -r requirements.lock

COPY alembic.ini ./
COPY app ./app
RUN mkdir -p /data/media /data/rustdesk /backups && chown -R connect:connect /data /backups

USER connect
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD curl -fsS http://127.0.0.1:8000/healthz >/dev/null || exit 1
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers --forwarded-allow-ips=${ROUTER_IP:-172.31.250.10} --no-server-header"]
