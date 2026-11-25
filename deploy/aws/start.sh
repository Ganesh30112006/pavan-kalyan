#!/bin/sh
# Brings the database schema up to date, then runs the API (with the in-process worker).
# Migrations are idempotent and additive while releases overlap (see docs/deployment.md, "Upgrades").
set -eu
python -m app.cli migrate
# First start-up only: creates the owner while the database has no users (password from Secrets Manager).
if [ -n "${CLOUDBRIDGE_BOOTSTRAP_EMAIL:-}" ]; then
    python -m app.cli ensure-owner --email "$CLOUDBRIDGE_BOOTSTRAP_EMAIL" \
        --name "${CLOUDBRIDGE_BOOTSTRAP_NAME:-Owner}" --organization "${CLOUDBRIDGE_BOOTSTRAP_ORGANIZATION:-CloudBridge}"
fi
unset CLOUDBRIDGE_BOOTSTRAP_PASSWORD
exec uvicorn app.main:create_app --factory --host 0.0.0.0 --port 8000 \
    --proxy-headers --no-access-log --timeout-graceful-shutdown 20
