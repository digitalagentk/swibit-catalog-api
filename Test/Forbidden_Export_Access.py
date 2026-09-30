"""Robustness case: Forbidden export access.

An export job and its generated file belong to the user who requested it.
User B must not be able to create an export of User A's list, inspect User
A's job, retry it, or download its result.
"""

from __future__ import annotations

import pytest

from Test.conftest import create_item, create_list


@pytest.fixture()
def alice_export(client, user_a):
    """User A's list plus an export job they created for it."""
    goal_id = create_list(client, user_a, "Alice's board")
    create_item(client, user_a, goal_id, "Alice's item")
    response = client.post(f"/goals/{goal_id}/exports", headers=user_a)
    assert response.status_code == 202, response.text
    return goal_id, response.json()["job_id"]


def test_user_b_cannot_export_user_a_list(client, user_b, alice_export):
    goal_id, _ = alice_export
    response = client.post(f"/goals/{goal_id}/exports", headers=user_b)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_user_b_cannot_inspect_user_a_export(client, user_b, alice_export):
    _, job_id = alice_export
    response = client.get(f"/exports/{job_id}", headers=user_b)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"


def test_user_b_cannot_download_user_a_export(client, user_b, alice_export):
    _, job_id = alice_export
    response = client.get(f"/exports/{job_id}/download", headers=user_b)
    assert response.status_code == 403


def test_user_b_cannot_retry_user_a_export(client, user_b, alice_export):
    _, job_id = alice_export
    response = client.post(f"/exports/{job_id}/retry", headers=user_b)
    assert response.status_code == 403


def test_export_history_only_contains_own_jobs(client, user_a, user_b, alice_export):
    _, job_id = alice_export

    bob_history = client.get("/exports", headers=user_b).json()
    assert bob_history["total"] == 0
    assert job_id not in [row["job_id"] for row in bob_history["items"]]

    alice_history = client.get("/exports", headers=user_a).json()
    assert alice_history["total"] == 1
    assert alice_history["items"][0]["job_id"] == job_id

