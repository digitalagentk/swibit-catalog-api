#!/bin/sh
# Container entrypoint.
#
# Applies the database migrations, then hands the process over to whatever
# command was passed (uvicorn by default). Keeping migrations here means a
# reviewer only ever needs `docker compose up` - no manual SQL, no separate
# migration step.
set -e

echo "[entrypoint] Applying database migrations (alembic upgrade head)..."
alembic upgrade head

echo "[entrypoint] Migrations applied. Starting: $*"
exec "$@"
