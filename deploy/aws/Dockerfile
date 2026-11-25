# CloudBridge in one container (web app + API + worker), as deployed to AWS ECS Express Mode by CI.
# Build from the repository root: docker build -f deploy/aws/Dockerfile -t cloudbridge-app .
# The API serves the web app on the same origin (CLOUDBRIDGE_STATIC_DIR), so the load balancer talks to one port.

FROM node:22-alpine AS web
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY frontend/ ./
# Same-origin API, never the demo backend. Only public values belong in VITE_* (they end up in the bundle).
ENV VITE_API_BASE_URL="" VITE_DEMO_MODE="false"
RUN npm run build \
    && if grep -l "createDemoApi" dist/assets/*.js; then echo "demo code in the bundle" >&2; exit 1; fi \
    && find dist -name '*.map' -delete \
    && rm -rf dist/__pycache__

FROM python:3.11-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv/cloudbridge
COPY backend/requirements.txt .
# setuptools and wheel only install packages; nothing imports them at runtime (fewer packages to patch).
RUN pip install -r requirements.txt && pip uninstall -y setuptools wheel
COPY backend/alembic.ini .
COPY backend/migrations ./migrations
COPY backend/app ./app
COPY --from=web /src/dist ./web
COPY deploy/aws/start.sh ./start.sh
RUN useradd --system --uid 10001 cloudbridge && chmod 0555 start.sh
USER cloudbridge
ENV CLOUDBRIDGE_STATIC_DIR=/srv/cloudbridge/web
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"]
CMD ["./start.sh"]
