"""Robustness case: Conflict.

Operations that violate a uniqueness or state constraint must fail with 409
and a specific machine-readable code, without corrupting stored data.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from Test.conftest import create_item, create_list, register


def test_duplicate_email_registration_is_rejected(client):
    assert register(client, "Alice", "alice@example.com").status_code == 201

    second = register(client, "Impostor", "alice@example.com")
    assert second.status_code == 409
    assert second.json()["error"]["code"] == "email_already_registered"


def test_duplicate_email_is_case_insensitive(client):
    assert register(client, "Alice", "alice@example.com").status_code == 201
    response = register(client, "Impostor", "ALICE@EXAMPLE.COM")
    assert response.status_code == 409


def test_conflicting_registration_does_not_change_the_original_account(client):
    register(client, "Alice", "alice@example.com", password="OriginalPass1!")
    register(client, "Impostor", "alice@example.com", password="HijackPass1!")

    # The original password still works and the conflicting one was never set.
    assert (
        client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "OriginalPass1!"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/auth/login",
            json={"email": "alice@example.com", "password": "HijackPass1!"},
        ).status_code
        == 401
    )


def test_retrying_a_completed_export_is_an_invalid_state_transition(client, user_a):
    goal_id = create_list(client, user_a)
    create_item(client, user_a, goal_id)

    job_id = client.post(f"/goals/{goal_id}/exports", headers=user_a).json()["job_id"]
    assert (
        client.get(f"/exports/{job_id}", headers=user_a).json()["status"] == "completed"
    )

    response = client.post(f"/exports/{job_id}/retry", headers=user_a)
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "invalid_state_transition"


def test_downloading_a_job_that_is_not_ready_is_a_conflict(client, user_a, monkeypatch):
    """A job that did not produce a file cannot be downloaded."""
    from Main import Export_Manager

    def _boom():
        raise OSError("simulated failure")

    original = Export_Manager.ensure_export_dir
    monkeypatch.setattr(Export_Manager, "ensure_export_dir", _boom)
    try:
        goal_id = create_list(client, user_a)
        job_id = client.post(f"/goals/{goal_id}/exports", headers=user_a).json()["job_id"]
        assert (
            client.get(f"/exports/{job_id}", headers=user_a).json()["status"] == "failed"
        )

        response = client.get(f"/exports/{job_id}/download", headers=user_a)
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "export_not_ready"
    finally:
        monkeypatch.setattr(Export_Manager, "ensure_export_dir", original)


def test_database_rejects_an_invalid_item_status(client, user_a, db_session):
    """The CHECK constraint holds even when the API layer is bypassed."""
    goal_id = create_list(client, user_a)

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO tasks (task_name, status, goal_id) "
                "VALUES ('bypass', 'banana', :goal_id)"
            ),
            {"goal_id": goal_id},
        )
        db_session.commit()
    db_session.rollback()


def test_database_rejects_an_invalid_list_status(client, user_a, db_session):
    """The goals CHECK constraint is equally unbypassable."""
    goal_id = create_list(client, user_a)

    with pytest.raises(IntegrityError):
        db_session.execute(
            text("UPDATE goals SET status = 'banana' WHERE goal_id = :goal_id"),
            {"goal_id": goal_id},
        )
        db_session.commit()
    db_session.rollback()

