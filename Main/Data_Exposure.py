"""List (goal) and Item (task) CRUD, scoped to the authenticated owner.

Every read and write is filtered by the owning ``user_id``, so cross-user
access is rejected in the application layer rather than merely documented.

Mapping onto the task's fixed shape:
    goal = **List**  (name + owner)
    task = **Item**  (title + status), belongs to exactly one list

Endpoint map
    GET    /goals                  paginated, own lists only
    POST   /goals                  create a list
    GET    /goals/{goal_id}        read one list
    PATCH  /goals/{goal_id}        rename a list
    DELETE /goals/{goal_id}        delete a list (cascades to its items)
    GET    /goals/{goal_id}/tasks  paginated items of one list
    POST   /goals/{goal_id}/tasks  create an item inside a list
    GET    /tasks/{task_id}        read one item
    PATCH  /tasks/{task_id}        update an item
    DELETE /tasks/{task_id}        delete one item
"""

from __future__ import annotations

from datetime import datetime
from typing import Generic, TypeVar

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from Main.Authentication import get_current_user
from Main.Database_Manager import Goal, GoalStatus, Task, TaskStatus, User, get_db
from Main.Errors import forbidden, not_found

router = APIRouter(tags=["Lists and items"])

T = TypeVar("T")


class Page(BaseModel, Generic[T]):
    """Envelope used by every collection response."""

    items: list[T]
    total: int
    limit: int
    offset: int


class GoalCreate(BaseModel):
    """Payload for creating a list."""

    goal_name: str = Field(min_length=1, max_length=200)
    status: GoalStatus = GoalStatus.ACTIVE


class GoalUpdate(BaseModel):
    """Partial update of a list - omitted fields are left unchanged."""

    goal_name: str | None = Field(default=None, min_length=1, max_length=200)
    status: GoalStatus | None = None


class GoalResponse(BaseModel):
    model_config = {"from_attributes": True}

    goal_id: int
    goal_name: str
    status: str
    user_id: int
    created_at: datetime


class TaskCreate(BaseModel):
    """Payload for creating an item."""

    task_name: str = Field(min_length=1, max_length=200)
    status: TaskStatus = TaskStatus.TODO


class TaskUpdate(BaseModel):
    """Partial update - omitted fields are left unchanged."""

    task_name: str | None = Field(default=None, min_length=1, max_length=200)
    status: TaskStatus | None = None


class TaskResponse(BaseModel):
    model_config = {"from_attributes": True}

    task_id: int
    task_name: str
    status: str
    goal_id: int
    created_at: datetime


# --------------------------------------------------------------------------
# Ownership helpers. These are the single place where authorization is
# decided for lists and items, so every endpoint behaves identically.
# --------------------------------------------------------------------------
def _get_owned_goal(db: Session, goal_id: int, user: User) -> Goal:
    """Load a list the caller owns, distinguishing 404 from 403."""
    goal = db.get(Goal, goal_id)
    if goal is None:
        raise not_found("List")
    if goal.user_id != user.user_id:
        raise forbidden("That list belongs to another user.")
    return goal


def _get_owned_task(db: Session, task_id: int, user: User) -> Task:
    """Load an item the caller owns, via its parent list."""
    task = db.get(Task, task_id)
    if task is None:
        raise not_found("Item")
    if task.goal.user_id != user.user_id:
        raise forbidden("That item belongs to another user.")
    return task


def _paginate(db: Session, statement, limit: int, offset: int) -> tuple[list, int]:
    """Return one page of rows plus the unpaged total."""
    total = db.scalar(select(func.count()).select_from(statement.subquery())) or 0
    rows = list(db.scalars(statement.limit(limit).offset(offset)).all())
    return rows, total


# --------------------------------------------------------------------------
# Lists (goals)
# --------------------------------------------------------------------------
@router.get("/goals", response_model=Page[GoalResponse])
def list_goals(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Page[GoalResponse]:
    """Return a page of the caller's own lists."""
    statement = (
        select(Goal)
        .where(Goal.user_id == current_user.user_id)
        .order_by(Goal.goal_id)
    )
    rows, total = _paginate(db, statement, limit, offset)
    return Page[GoalResponse](
        items=[GoalResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/goals", status_code=201, response_model=GoalResponse)
def create_goal(
    payload: GoalCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Goal:
    """Create a list owned by the caller."""
    goal = Goal(
        goal_name=payload.goal_name,
        status=payload.status.value,
        user_id=current_user.user_id,
    )
    db.add(goal)
    db.commit()
    db.refresh(goal)
    return goal


@router.get("/goals/{goal_id}", response_model=GoalResponse)
def read_goal(
    goal_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Goal:
    """Read one of the caller's own lists."""
    return _get_owned_goal(db, goal_id, current_user)


@router.patch("/goals/{goal_id}", response_model=GoalResponse)
def update_goal(
    goal_id: int,
    payload: GoalUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Goal:
    """Update one of the caller's own lists (name and/or status)."""
    goal = _get_owned_goal(db, goal_id, current_user)
    if payload.goal_name is not None:
        goal.goal_name = payload.goal_name
    if payload.status is not None:
        goal.status = payload.status.value
    db.commit()
    db.refresh(goal)
    return goal


@router.delete("/goals/{goal_id}", status_code=204)
def delete_goal(
    goal_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete one of the caller's own lists, cascading to its items."""
    goal = _get_owned_goal(db, goal_id, current_user)
    db.delete(goal)
    db.commit()


# --------------------------------------------------------------------------
# Items (tasks). Every item lives inside a list, so ownership is always
# resolved through the parent goal.
# --------------------------------------------------------------------------
@router.get("/goals/{goal_id}/tasks", response_model=Page[TaskResponse])
def list_tasks(
    goal_id: int,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Page[TaskResponse]:
    """Return a page of items inside one of the caller's own lists."""
    _get_owned_goal(db, goal_id, current_user)
    statement = select(Task).where(Task.goal_id == goal_id).order_by(Task.task_id)
    rows, total = _paginate(db, statement, limit, offset)
    return Page[TaskResponse](
        items=[TaskResponse.model_validate(row) for row in rows],
        total=total,
        limit=limit,
        offset=offset,
    )


@router.post("/goals/{goal_id}/tasks", status_code=201, response_model=TaskResponse)
def create_task(
    goal_id: int,
    payload: TaskCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Task:
    """Create an item inside one of the caller's own lists.

    Adding an item to another user's list is rejected here, because the
    ownership check runs before the row is built.
    """
    _get_owned_goal(db, goal_id, current_user)
    task = Task(
        task_name=payload.task_name,
        status=payload.status.value,
        goal_id=goal_id,
    )
    db.add(task)
    db.commit()
    db.refresh(task)
    return task


@router.get("/tasks/{task_id}", response_model=TaskResponse)
def read_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Task:
    """Read one of the caller's own items."""
    return _get_owned_task(db, task_id, current_user)


@router.patch("/tasks/{task_id}", response_model=TaskResponse)
def update_task(
    task_id: int,
    payload: TaskUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> Task:
    """Update an item's title and/or status."""
    task = _get_owned_task(db, task_id, current_user)
    if payload.task_name is not None:
        task.task_name = payload.task_name
    if payload.status is not None:
        task.status = payload.status.value
    db.commit()
    db.refresh(task)
    return task


@router.delete("/tasks/{task_id}", status_code=204)
def delete_task(
    task_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
) -> None:
    """Delete one of the caller's own items."""
    task = _get_owned_task(db, task_id, current_user)
    db.delete(task)
    db.commit()

