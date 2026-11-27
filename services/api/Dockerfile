# One image: the API plus the built web app.
# Build from the repo root:  docker build -f services/api/Dockerfile .
FROM node:22-slim AS web
WORKDIR /web
COPY apps/web/package*.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web ./
RUN npm run build

FROM python:3.13-slim
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv/carelens
COPY services/api/requirements.txt services/api/requirements.txt
RUN pip install --no-cache-dir -r services/api/requirements.txt
COPY services/api services/api
COPY data data
COPY --from=web /web/dist apps/web/dist
RUN useradd --system --home /srv/carelens carelens && chown -R carelens /srv/carelens
USER carelens
WORKDIR /srv/carelens/services/api
EXPOSE 8000
# Hosts such as Render pass the port in $PORT. One worker: sandboxes and the
# reminder loop keep their indexes in this process (see app/sandbox.py).
CMD ["sh", "-c", "exec python -m uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers 1 --proxy-headers --forwarded-allow-ips='*'"]
