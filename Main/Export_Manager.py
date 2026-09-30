"""Background export of a goal (List) into a downloadable CSV file.

Flow
    POST /goals/{goal_id}/exports   -> 202 immediately; job row is `pending`
    (background task)               -> `running` -> `completed` | `failed`
    GET  /exports/{job_id}          -> poll status
    GET  /exports/{job_id}/download -> stream the CSV once completed
    GET  /exports                   -> paginated history of own jobs
    POST /exports/{job_id}/retry    -> re-run a `failed` job

Reliability trade-off: the worker runs in-process through FastAPI's
``BackgroundTasks``. There is no broker, so a crash mid-export leaves a job
stuck in ``running`` and jobs do not survive a restart. That is an accepted
limitation at this scope; DESIGN.md describes the durable-queue upgrade.
"""

from __future__ import annotations

import csv
import os
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from Main.Authentication import get_current_user
from Main.Data_Exposure import Page
from Main.Database_Manager import (
    ExportJob,
    ExportStatus,
    Goal,
    SessionLocal,
    Task,
    User,
    get_db,
)
from Main.Errors import conflict, forbidden, not_found

router = APIRouter(tags=["Exports"])

# Where generated files land. Exposed as EXPORT_DIR so Docker can mount it.
EXPORT_DIR = Path(os.getenv("EXPORT_DIR", "./exports"))

# Session factory used by the background worker. It is a module-level name so
# the test suite can point the worker at its own database.
session_factory = SessionLocal


def ensure_export_dir() -> Path:
    """Create the export directory on demand and return it."""
    EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    return EXPORT_DIR


class ExportJobResponse(BaseModel):
    """Public view of an export job."""

    model_config = {"from_attributes": True}

    job_id: int
    goal_id: int
    user_id: int
    status: str
    file_path: str | None
    error: str | None
    created_at: datetime
    updated_at: datetime


def run_export(job_id: int) -> None:
    """Render one export job's CSV and record the outcome.

    This runs after the HTTP response has been sent, so it opens its own
    session rather than reusing the request-scoped one (already closed).
    """
    db = session_factory()
    try:
        job = db.get(ExportJob, job_id)
        if job is None:
            return

        job.status = ExportStatus.RUNNING.value
        db.commit()

        goal = db.get(Goal, job.goal_id)
        tasks = list(
            db.scalars(
                select(Task).where(Task.goal_id == job.goal_id).order_by(Task.task_id)
            ).all()
        )

        destination = ensure_export_dir() / f"goal_{job.goal_id}_job_{job.job_id}.csv"
        with destination.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(
                [
                    "goal_id",
                    "goal_name",
                    "goal_status",
                    "task_id",
                    "task_name",
                    "task_status",
                ]
            )
            for task in tasks:
                writer.writerow(
                    [
                        goal.goal_id,
                        goal.goal_name,
                        goal.status,
                        task.task_id,
                        task.task_name,
                        task.status,
                    ]
                )

        job.file_path = str(destination)
        job.error = None
        job.status = ExportStatus.COMPLETED.value
        db.commit()
    except Exception as exc:  # noqa: BLE001 - the job must record any failure
        db.rollback()
        job = db.get(ExportJob, job_id)
        if job is not None:
            job.status = ExportStatus.FAILED.value
            job.error = f"{type(exc).__name__}: {exc}"[:500]
            db.commit()
    finally:
        db.close()


def _get_owned_job(db: Session, job_id: int, user: User) -> ExportJob:
    """Load an export job the caller owns, distinguishing 404 from 403."""
    job = db.get(ExportJob, job_id)
    if job is None:
        raise not_found("Export job")
    if job.user_id != user.user_id:
        raise forbidden("That export job belongs to another user.")
    return job


@router.post(
    "/goals/{goal_id}/exports", status_code=202, response_model=ExportJobResponse
)
def create_export(
    goal_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ExportJob:
    """Queue an export of one of the caller's own lists.

    Responds 202 straight away with a ``pending`` job; the CSV is produced
    by a background task after this response has been sent.
    """
    goal = db.get(Goal, goal_id)
    if goal is None:
        raise not_found("List")
    if goal.user_id != current_user.user_id:
        raise forbidden("That list belongs to another user.")

    job = ExportJob(
        goal_id=goal_id,
        user_id=current_user.user_id,
        status=ExportStatus.PENDING.value,
    )
    db.add(job)
    db.commit()
    db.refresh(job)

    background_tasks.add_task(run_export, job.job_id)
    return job


@router.get("/exports", response_model=Page[ExportJobResponse])
def list_exports(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Page[ExportJobResponse]:
    """Return a page of the caller's own export jobs, newest first."""
    statement = (
        select(ExportJob)
        .where(ExportJob.user_id == current_user.user_id)
        .order_by(ExportJob.job_id.desc())
    )
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = list(db.scalars(statement.limit(limit).offset(offset)).all())
    return Page[ExportJobResponse](
        items=[ExportJobResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.get("/exports/{job_id}", response_model=ExportJobResponse)
def read_export(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ExportJob:
    """Poll the status of one of the caller's own export jobs."""
    return _get_owned_job(db, job_id, current_user)


@router.get("/exports/{job_id}/download")
def download_export(
    job_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> FileResponse:
    """Download the generated CSV once the job has completed."""
    job = _get_owned_job(db, job_id, current_user)
    if job.status != ExportStatus.COMPLETED.value or not job.file_path:
        raise conflict(
            "export_not_ready",
            f"Export job is '{job.status}'; the file exists only once 'completed'.",
        )

    path = Path(job.file_path)
    if not path.is_file():
        # Row says completed but the artefact is gone - report it honestly
        # rather than streaming a 500.
        raise not_found("Export file")
    return FileResponse(path, media_type="text/csv", filename=path.name)


@router.post(
    "/exports/{job_id}/retry", status_code=202, response_model=ExportJobResponse
)
def retry_export(
    job_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> ExportJob:
    """Re-run an export job, but only when it previously failed.

    Retrying anything else is an invalid state transition -> 409.
    """
    job = _get_owned_job(db, job_id, current_user)
    if job.status != ExportStatus.FAILED.value:
        raise conflict(
            "invalid_state_transition",
            f"Only a 'failed' export can be retried (job is '{job.status}').",
        )

    job.status = ExportStatus.PENDING.value
    job.error = None
    db.commit()
    db.refresh(job)

    background_tasks.add_task(run_export, job.job_id)
    return job
