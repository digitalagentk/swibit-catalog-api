"""PostgreSQL wiring for the Swibit Catalog API.

Owns the SQLAlchemy engine and session factory, the ORM models, and the
helpers the API uses to boot against Postgres inside Docker.

Domain mapping onto the task's fixed shape (see DESIGN.md):

    ============  ==================================================
    users         account and login identity
    goals         a **List**  (``goal_name`` = list name, ``user_id`` = owner)
    tasks         an **Item** (``task_name`` = title, ``status`` = category)
    export_jobs   a background export of one goal/list
    ============  ==================================================
"""

from __future__ import annotations

import enum
import os
import time
from collections.abc import Iterator
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    String,
    Text,
    create_engine,
    func,
    text,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

# Host port 5433 matches the port docker-compose publishes for the `db`
# service (see docker-compose.yml) so local runs work out of the box.
DEFAULT_DATABASE_URL = "postgresql+psycopg://swibit:swibit@localhost:5433/swibit_catalog"


class TaskStatus(enum.StrEnum):
    """Allowed values for ``Task.status`` (an Item's status/category)."""

    TODO = "todo"
    IN_PROGRESS = "in_progress"
    DONE = "done"


class GoalStatus(enum.StrEnum):
    """Allowed values for ``Goal.status`` (a List's own lifecycle)."""

    ACTIVE = "active"
    ON_HOLD = "on_hold"
    COMPLETED = "completed"
    ARCHIVED = "archived"


class ExportStatus(enum.StrEnum):
    """Lifecycle of a background export job."""

    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


TASK_STATUS_VALUES: tuple[str, ...] = tuple(s.value for s in TaskStatus)
GOAL_STATUS_VALUES: tuple[str, ...] = tuple(s.value for s in GoalStatus)
EXPORT_STATUS_VALUES: tuple[str, ...] = tuple(s.value for s in ExportStatus)


def get_database_url() -> str:
    """Resolve the connection string (env override, else the local default)."""
    return os.getenv("DATABASE_URL", DEFAULT_DATABASE_URL)


class Base(DeclarativeBase):
    """Declarative base shared by every ORM model."""


class User(Base):
    """An account: the login identity and the owner of goals (lists)."""

    __tablename__ = "users"

    user_id: Mapped[int] = mapped_column(primary_key=True)
    user_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # Email is the login identifier and carries the UNIQUE constraint that
    # drives the "duplicate registration" conflict case.
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    # Only ever a hash - plaintext is never stored, returned, or logged.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # `default` covers ORM inserts; `server_default` makes PostgreSQL itself
    # apply the fallback so raw SQL inserts cannot violate NOT NULL.
    user_privilege: Mapped[str] = mapped_column(
        String(20), nullable=False, default="user", server_default="user"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    goals: Mapped[list[Goal]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class Goal(Base):
    """A **List**: a named collection owned by exactly one user."""

    __tablename__ = "goals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('active', 'on_hold', 'completed', 'archived')",
            name="ck_goals_status",
        ),
    )

    goal_id: Mapped[int] = mapped_column(primary_key=True)
    goal_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=GoalStatus.ACTIVE.value,
        server_default=GoalStatus.ACTIVE.value,
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    user: Mapped[User] = relationship(back_populates="goals")
    tasks: Mapped[list[Task]] = relationship(
        back_populates="goal", cascade="all, delete-orphan"
    )


class Task(Base):
    """An **Item**: belongs to exactly one goal/list and carries a status."""

    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('todo', 'in_progress', 'done')", name="ck_tasks_status"
        ),
    )

    task_id: Mapped[int] = mapped_column(primary_key=True)
    task_name: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=TaskStatus.TODO.value,
        server_default=TaskStatus.TODO.value,
    )
    goal_id: Mapped[int] = mapped_column(
        ForeignKey("goals.goal_id", ondelete="CASCADE"), nullable=False, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    goal: Mapped[Goal] = relationship(back_populates="tasks")


class ExportJob(Base):
    """A background export of one goal/list into a downloadable file."""

    __tablename__ = "export_jobs"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'running', 'completed', 'failed')",
            name="ck_export_jobs_status",
        ),
    )

    job_id: Mapped[int] = mapped_column(primary_key=True)
    goal_id: Mapped[int] = mapped_column(
        ForeignKey("goals.goal_id", ondelete="CASCADE"), nullable=False, index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default=ExportStatus.PENDING.value,
        server_default=ExportStatus.PENDING.value,
    )
    file_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    goal: Mapped[Goal] = relationship()
    user: Mapped[User] = relationship()


# The engine is lazy: it only opens a socket on the first real query, so
# importing this module never fails just because Postgres is not up yet.
engine: Engine = create_engine(get_database_url(), pool_pre_ping=True, future=True)
SessionLocal = sessionmaker(
    bind=engine, autoflush=False, autocommit=False, expire_on_commit=False
)


def get_db() -> Iterator[Session]:
    """FastAPI dependency that yields a request-scoped session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def ping_database() -> bool:
    """Return ``True`` when PostgreSQL answers a trivial query."""
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def wait_for_database(max_attempts: int = 15, delay: float = 2.0) -> None:
    """Block until Postgres accepts connections.

    Compose starts the database alongside the API, so the API retries
    instead of crash-looping while Postgres finishes its first boot.
    """
    for attempt in range(1, max_attempts + 1):
        if ping_database():
            print(f"[Database_Manager] Postgres reachable (attempt {attempt}).")
            return
        print(f"[Database_Manager] Waiting for Postgres ({attempt}/{max_attempts})...")
        time.sleep(delay)

    raise RuntimeError(
        "Postgres unreachable at "
        f"{engine.url.render_as_string(hide_password=True)} "
        f"after {max_attempts} attempts."
    )


def init_db() -> None:
    """Create any missing tables straight from the ORM metadata.

    This is the fast path used by the test suite and by local development.
    The running application applies schema changes through Alembic
    migrations instead (``alembic upgrade head``).
    """
    Base.metadata.create_all(bind=engine)
    tables = ", ".join(sorted(Base.metadata.tables))
    print(f"[Database_Manager] Schema ready: {tables}")


def drop_all() -> None:
    """Drop every table (used by the test suite to reset state)."""
    Base.metadata.drop_all(bind=engine)

