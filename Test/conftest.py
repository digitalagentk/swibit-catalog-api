"""Shared pytest fixtures.

The suite runs against a dedicated PostgreSQL database (``swibit_catalog_test``
by default) so it can never touch development data. The schema is built from
the ORM metadata rather than by replaying migrations, which keeps the suite
fast and independent of migration ordering.

Prerequisites: the database must be reachable (``docker compose up -d db``).

    pytest -q                      # from the project root
    TEST_DATABASE_URL=... pytest   # override the target database
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.orm import Session, sessionmaker

from Main import Export_Manager
from Main.Database_Manager import Base, get_db
from Main.Entry import app

DEFAULT_TEST_URL = (
    "postgresql+psycopg://swibit:swibit@localhost:5433/swibit_catalog_test"
)
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", DEFAULT_TEST_URL)

# Order matters only for readability; CASCADE does the real work.
TABLES = "export_jobs, tasks, goals, users"


def _ensure_database(url: str) -> None:
    """Create the target database if it does not exist yet."""
    target = make_url(url)
    admin_engine = create_engine(
        target.set(database="postgres"), isolation_level="AUTOCOMMIT"
    )
    try:
        with admin_engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :name"),
                {"name": target.database},
            ).scalar()
            if not exists:
                connection.execute(text(f'CREATE DATABASE "{target.database}"'))
    finally:
        admin_engine.dispose()


@pytest.fixture(scope="session")
def engine() -> Iterator[Engine]:
    """Session-wide test engine with the schema created once."""
    _ensure_database(TEST_DATABASE_URL)
    test_engine = create_engine(TEST_DATABASE_URL, pool_pre_ping=True)
    Base.metadata.create_all(bind=test_engine)
    yield test_engine
    test_engine.dispose()


@pytest.fixture()
def db_session(engine: Engine) -> Iterator[Session]:
    """A session on an empty database, truncated before each test."""
    with engine.begin() as connection:
        connection.execute(
            text(f"TRUNCATE TABLE {TABLES} RESTART IDENTITY CASCADE")
        )
    factory = sessionmaker(
        bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
    )
    session = factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(autouse=True)
def _isolate_export_worker(
    engine: Engine, monkeypatch: pytest.MonkeyPatch, tmp_path
) -> None:
    """Point the background export worker at the test DB and a temp dir."""
    factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(Export_Manager, "session_factory", factory)
    monkeypatch.setattr(Export_Manager, "EXPORT_DIR", tmp_path / "exports")


@pytest.fixture()
def client(db_session: Session) -> Iterator[TestClient]:
    """A TestClient whose database dependency is the test session.

    The lifespan is intentionally not run: the suite should not need the
    application's own database to be up.
    """
    app.dependency_overrides[get_db] = lambda: db_session
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.clear()


# --------------------------------------------------------------------------
# Convenience helpers used by several test modules
# --------------------------------------------------------------------------
def register(client: TestClient, name: str, email: str, password: str = "Sup3rSecret!"):
    """Register an account and return the raw response."""
    return client.post(
        "/auth/register",
        json={"user_name": name, "email": email, "password": password},
    )


def login(client: TestClient, email: str, password: str = "Sup3rSecret!") -> str:
    """Log in and return the bearer token."""
    response = client.post("/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def auth(token: str) -> dict[str, str]:
    """Build an Authorization header."""
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture()
def user_a(client: TestClient) -> dict[str, str]:
    """Register and authenticate User A, returning headers."""
    register(client, "Alice", "alice@example.com")
    return auth(login(client, "alice@example.com"))


@pytest.fixture()
def user_b(client: TestClient) -> dict[str, str]:
    """Register and authenticate User B, returning headers."""
    register(client, "Bob", "bob@example.com")
    return auth(login(client, "bob@example.com"))


def create_list(
    client: TestClient,
    headers: dict[str, str],
    name: str = "Board",
    status: str | None = None,
) -> int:
    """Create a list as the given user and return its id."""
    payload: dict[str, object] = {"goal_name": name}
    if status is not None:
        payload["status"] = status
    response = client.post("/goals", json=payload, headers=headers)
    assert response.status_code == 201, response.text
    return response.json()["goal_id"]


def create_item(
    client: TestClient,
    headers: dict[str, str],
    goal_id: int,
    name: str = "An item",
    status: str = "todo",
) -> int:
    """Create an item inside a list and return its id."""
    response = client.post(
        f"/goals/{goal_id}/tasks",
        json={"task_name": name, "status": status},
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["task_id"]
