# Build from the repo root:  docker build -f services/api/Dockerfile .
FROM node:22-slim AS web
WORKDIR /web
COPY apps/web/package*.json ./
RUN npm ci --no-audit --no-fund
COPY apps/web ./
RUN npm run build

FROM python:3.13-slim
WORKDIR /srv/carelens
COPY services/api/requirements.txt services/api/requirements.txt
RUN pip install --no-cache-dir -r services/api/requirements.txt
COPY services/api services/api
COPY data data
COPY --from=web /web/dist apps/web/dist
WORKDIR /srv/carelens/services/api
EXPOSE 8000
CMD ["sh", "-c", "alembic upgrade head && python -m uvicorn app.main:app --host 0.0.0.0 --port 8000"]
