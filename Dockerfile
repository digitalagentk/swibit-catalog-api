# syntax=docker/dockerfile:1
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Dependencies first so this layer stays cached when only app code changes.
# psycopg[binary] bundles libpq, so no build-essential/libpq-dev needed.
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Application code, migration environment and the startup entrypoint.
COPY Main ./Main
COPY alembic ./alembic
COPY alembic.ini ./
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh

# The test suite ships in the image so `docker compose exec api pytest`
# works for a reviewer without installing anything locally.
COPY Test ./Test
COPY pytest.ini ./

EXPOSE 8000

# Reports healthy only when the API answers AND Postgres is reachable.
HEALTHCHECK --interval=15s --timeout=5s --start-period=25s --retries=5 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4)" || exit 1

# entrypoint.sh runs `alembic upgrade head`, then execs the command below.
# No --reload here: reload is for local development only.
ENTRYPOINT ["./entrypoint.sh"]
CMD ["uvicorn", "Main.Entry:app", "--host", "0.0.0.0", "--port", "8000"]
