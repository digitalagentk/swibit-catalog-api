"""Automated test: the background export lifecycle.

Covers the required "export lifecycle" category end to end: a job is created
and accepted immediately, its status transitions as the worker runs, the
artefact can be downloaded, and a failure is recorded without disturbing the
list or its items.
"""

from __future__ import annotations

from Main.Database_Manager import EXPORT_STATUS_VALUES
from Test.conftest import create_item, create_list


def test_export_request_is_accepted_immediately_with_a_job_record(client, user_a):
    """The API must not block until the file has been produced."""
    goal_id = create_list(client, user_a, "Alice's board")

    response = client.post(f"/goals/{goal_id}/exports", headers=user_a)
    assert response.status_code == 202
    body = response.json()
    assert body["status"] in EXPORT_STATUS_VALUES
    assert body["status"] == "pending"
    assert body["file_path"] is None
    assert body["error"] is None
    assert body["goal_id"] == goal_id
    assert body["user_id"] > 0


def test_export_runs_to_completion_and_can_be_downloaded(client, user_a):
    goal_id = create_list(client, user_a, "Alice's board", status="on_hold")
    create_item(client, user_a, goal_id, "Write the README", status="in_progress")
    create_item(client, user_a, goal_id, "Add tests", status="done")

    job_id = client.post(f"/goals/{goal_id}/exports", headers=user_a).json()["job_id"]

    # TestClient executes BackgroundTasks inline, so the next read observes
    # the terminal state rather than needing a sleep/poll loop.
    finished = client.get(f"/exports/{job_id}", headers=user_a)
    assert finished.status_code == 200
    assert finished.json()["status"] == "completed"
    assert finished.json()["error"] is None

    download = client.get(f"/exports/{job_id}/download", headers=user_a)
    assert download.status_code == 200
    assert download.headers["content-type"].startswith("text/csv")

    lines = download.text.strip().splitlines()
    assert lines[0] == (
        "goal_id,goal_name,goal_status,task_id,task_name,task_status"
    )
    assert len(lines) == 3  # header + the two items
    assert "Alice's board" in download.text
    assert "on_hold" in download.text
    assert "Write the README" in download.text
    assert "in_progress" in download.text
    assert "done" in download.text


def test_list_status_round_trips_through_create_read_and_update(client, user_a):
    """The list's own status is settable, readable and updatable."""
    goal_id = create_list(client, user_a, "Statusful board", status="completed")

    created = client.get(f"/goals/{goal_id}", headers=user_a).json()
    assert created["status"] == "completed"

    updated = client.patch(
        f"/goals/{goal_id}", json={"status": "archived"}, headers=user_a
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "archived"

    # A partial update that omits status must not reset it.
    renamed = client.patch(
        f"/goals/{goal_id}", json={"goal_name": "Still archived"}, headers=user_a
    )
    assert renamed.json()["goal_name"] == "Still archived"
    assert renamed.json()["status"] == "archived"


def test_export_failure_is_recorded_without_corrupting_the_list(
    client, user_a, monkeypatch
):
    """A failed export must leave the list and its items untouched."""
    from Main import Export_Manager

    def _boom():
        raise OSError("simulated disk failure")

    goal_id = create_list(client, user_a, "Fragile board")
    create_item(client, user_a, goal_id, "Survives a failed export")

    monkeypatch.setattr(Export_Manager, "ensure_export_dir", _boom)
    job_id = client.post(f"/goals/{goal_id}/exports", headers=user_a).json()["job_id"]

    job = client.get(f"/exports/{job_id}", headers=user_a).json()
    assert job["status"] == "failed"
    assert "simulated disk failure" in job["error"]
    assert job["file_path"] is None

    # The list and its single item are exactly as they were.
    assert client.get(f"/goals/{goal_id}", headers=user_a).status_code == 200
    assert client.get(f"/goals/{goal_id}/tasks", headers=user_a).json()["total"] == 1


def test_a_failed_export_can_be_retried(client, user_a, monkeypatch):
    from Main import Export_Manager

    def _boom():
        raise OSError("simulated disk failure")

    original = Export_Manager.ensure_export_dir
    monkeypatch.setattr(Export_Manager, "ensure_export_dir", _boom)
    try:
        goal_id = create_list(client, user_a, "Retryable board")
        job_id = client.post(f"/goals/{goal_id}/exports", headers=user_a).json()["job_id"]
        assert (
            client.get(f"/exports/{job_id}", headers=user_a).json()["status"] == "failed"
        )
    finally:
        monkeypatch.setattr(Export_Manager, "ensure_export_dir", original)

    retry = client.post(f"/exports/{job_id}/retry", headers=user_a)
    assert retry.status_code == 202
    assert retry.json()["status"] == "pending"

    retried = client.get(f"/exports/{job_id}", headers=user_a).json()
    assert retried["status"] == "completed"
    assert retried["error"] is None
    assert client.get(f"/exports/{job_id}/download", headers=user_a).status_code == 200


def test_export_history_is_paginated_newest_first(client, user_a):
    goal_id = create_list(client, user_a)
    for _ in range(3):
        assert client.post(f"/goals/{goal_id}/exports", headers=user_a).status_code == 202

    first_page = client.get("/exports?limit=2&offset=0", headers=user_a).json()
    assert first_page["total"] == 3
    assert first_page["limit"] == 2
    assert len(first_page["items"]) == 2
    assert first_page["items"][0]["job_id"] > first_page["items"][1]["job_id"]

    second_page = client.get("/exports?limit=2&offset=2", headers=user_a).json()
    assert len(second_page["items"]) == 1
