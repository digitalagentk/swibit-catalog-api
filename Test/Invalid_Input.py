"""Robustness case: Invalid input.

Missing required fields, wrong types, out-of-range values, and values that
violate business rules must all fail with 422 plus field-level detail, and
must never leave partial state behind.
"""

from __future__ import annotations

import pytest

from Test.conftest import create_item, create_list
from Main.Database_Manager import GOAL_STATUS_VALUES, TASK_STATUS_VALUES


def _fields(response) -> set[str]:
    """The set of field paths reported by a validation failure."""
    return {detail["field"] for detail in response.json()["error"]["details"]}


def test_register_rejects_missing_required_fields(client):
    response = client.post("/auth/register", json={"email": "someone@example.com"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_input"
    fields = _fields(response)
    assert any("user_name" in field for field in fields)
    assert any("password" in field for field in fields)


def test_register_rejects_malformed_email(client):
    response = client.post(
        "/auth/register",
        json={"user_name": "X", "email": "not-an-email", "password": "Sup3rSecret!"},
    )
    assert response.status_code == 422
    assert any("email" in field for field in _fields(response))


def test_register_rejects_short_password(client):
    response = client.post(
        "/auth/register",
        json={"user_name": "X", "email": "x@example.com", "password": "short"},
    )
    assert response.status_code == 422
    assert any("password" in field for field in _fields(response))


def test_login_rejects_missing_password(client):
    response = client.post("/auth/login", json={"email": "x@example.com"})
    assert response.status_code == 422


@pytest.mark.parametrize(
    "payload",
    [{}, {"goal_name": ""}, {"goal_name": "x" * 201}, {"goal_name": 123}],
)
def test_create_list_rejects_bad_payloads(client, user_a, payload):
    response = client.post("/goals", json=payload, headers=user_a)
    assert response.status_code == 422, payload


def test_create_list_rejects_unknown_status(client, user_a):
    response = client.post(
        "/goals", json={"goal_name": "Board", "status": "banana"}, headers=user_a
    )
    assert response.status_code == 422
    assert any("status" in field for field in _fields(response))
    # The rejected list was never stored.
    assert client.get("/goals", headers=user_a).json()["total"] == 0


def test_update_list_rejects_unknown_status(client, user_a):
    goal_id = create_list(client, user_a)
    response = client.patch(f"/goals/{goal_id}", json={"status": "nope"}, headers=user_a)
    assert response.status_code == 422
    # The stored status is unchanged.
    assert client.get(f"/goals/{goal_id}", headers=user_a).json()["status"] == "active"


def test_rejected_list_creation_writes_nothing(client, user_a):
    client.post("/goals", json={"goal_name": ""}, headers=user_a)
    assert client.get("/goals", headers=user_a).json()["total"] == 0


def test_create_item_rejects_unknown_status(client, user_a):
    goal_id = create_list(client, user_a)
    response = client.post(
        f"/goals/{goal_id}/tasks",
        json={"task_name": "An item", "status": "banana"},
        headers=user_a,
    )
    assert response.status_code == 422
    assert client.get(f"/goals/{goal_id}/tasks", headers=user_a).json()["total"] == 0


@pytest.mark.parametrize(
    "payload",
    [{"task_name": ""}, {"task_name": "x" * 201}, {"task_name": "ok", "status": 5}],
)
def test_create_item_rejects_bad_payloads(client, user_a, payload):
    goal_id = create_list(client, user_a)
    response = client.post(f"/goals/{goal_id}/tasks", json=payload, headers=user_a)
    assert response.status_code == 422, payload


def test_update_item_rejects_unknown_status(client, user_a):
    goal_id = create_list(client, user_a)
    task_id = create_item(client, user_a, goal_id)
    response = client.patch(
        f"/tasks/{task_id}", json={"status": "nope"}, headers=user_a
    )
    assert response.status_code == 422
    # The stored value is unchanged.
    assert client.get(f"/tasks/{task_id}", headers=user_a).json()["status"] == "todo"


@pytest.mark.parametrize("query", ["limit=0", "limit=101", "offset=-1", "limit=abc"])
def test_pagination_parameters_are_validated(client, user_a, query):
    response = client.get(f"/goals?{query}", headers=user_a)
    assert response.status_code == 422, query


@pytest.mark.parametrize("status", GOAL_STATUS_VALUES)
def test_every_valid_list_status_is_accepted(client, user_a, status):
    """Each documented list status round-trips through the API."""
    response = client.post(
        "/goals", json={"goal_name": "Board", "status": status}, headers=user_a
    )
    assert response.status_code == 201, status
    assert response.json()["status"] == status


@pytest.mark.parametrize("status", TASK_STATUS_VALUES)
def test_every_valid_item_status_is_accepted(client, user_a, status):
    """Each documented item status round-trips through the API."""
    goal_id = create_list(client, user_a)
    response = client.post(
        f"/goals/{goal_id}/tasks",
        json={"task_name": "An item", "status": status},
        headers=user_a,
    )
    assert response.status_code == 201, status
    assert response.json()["status"] == status

