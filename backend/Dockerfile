# CloudBridge API and worker (one image). Build: docker build -t cloudbridge-api backend/
#   API:       the default command (set CLOUDBRIDGE_RUN_WORKER=false when the worker runs on its own)
#   worker:    python -m app.worker
#   migrations python -m app.cli migrate   (once per release, before new API processes start)
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /srv/cloudbridge

COPY requirements.txt .
# setuptools and wheel only install packages; nothing imports them at runtime (fewer packages to patch).
RUN pip install -r requirements.txt && pip uninstall -y setuptools wheel

COPY alembic.ini .
COPY migrations ./migrations
COPY app ./app

RUN useradd --system --uid 10001 cloudbridge
USER cloudbridge

EXPOSE 8000
# Liveness only (the process answers); orchestrators should route traffic on /api/v1/ready instead.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=4)"]
# Requests are logged by the app itself (with their X-Request-ID), so uvicorn's access log is off.
# On SIGTERM, open requests get 20 s to finish; the in-process worker then stops between steps.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--proxy-headers", "--no-access-log", "--timeout-graceful-shutdown", "20"]
