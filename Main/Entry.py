"""Entry point for the Swibit Catalog API.

Bootstraps the FastAPI application, wires it to PostgreSQL, mounts the
routers, and starts uvicorn when the module is executed directly.

Run with Docker (API + PostgreSQL), which also applies migrations:
    docker compose up --build

Run locally (needs a reachable Postgres, e.g. `docker compose up -d db`):
    alembic upgrade head          # create/refresh the schema
    python3 -m Main.Entry
    # or
    uvicorn Main.Entry:app --reload --port 8000
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from Main.Authentication import router as authentication_router
from Main.Data_Exposure import router as data_router
from Main.Database_Manager import ping_database, wait_for_database
from Main.Errors import register_error_handlers
from Main.Export_Manager import router as export_router

APP_NAME = "Swibit Catalog API"
APP_VERSION = "0.2.0"


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Wait for Postgres to accept connections before serving traffic.

    The schema itself is owned by Alembic (``alembic upgrade head``), which
    the container entrypoint runs before uvicorn starts. We deliberately do
    not call ``create_all`` here so the database can never drift from the
    migration history.
    """
    wait_for_database()
    yield


app = FastAPI(title=APP_NAME, version=APP_VERSION, lifespan=lifespan)

register_error_handlers(app)
app.include_router(authentication_router)
app.include_router(data_router)
app.include_router(export_router)


@app.get("/", tags=["Health"])
def hello_world() -> dict[str, str]:
    """Smoke-test endpoint confirming the entry point is wired up."""
    return {"message": "Hello World", "service": APP_NAME, "version": APP_VERSION}


@app.get("/health", tags=["Health"])
def health() -> JSONResponse:
    """Liveness probe that also reports PostgreSQL connectivity.

    Returns 200 when Postgres answers, 503 otherwise, so Docker's
    HEALTHCHECK (and the test suite) can tell the two states apart.
    """
    database_up = ping_database()
    return JSONResponse(
        status_code=200 if database_up else 503,
        content={
            "status": "ok" if database_up else "degraded",
            "database": "up" if database_up else "down",
        },
    )


def main() -> None:
    """Run the development server (used by `python3 -m Main.Entry`)."""
    import uvicorn

    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))
    uvicorn.run("Main.Entry:app", host=host, port=port, reload=True)


if __name__ == "__main__":
    main()

