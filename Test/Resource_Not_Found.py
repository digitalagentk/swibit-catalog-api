"""Robustness case: Resource not found.

Operating on an id that does not exist must fail with a consistent 404, and
a second delete must report 404 rather than pretending to succeed.
"""

from __future__ import annotations

from Test.conftest import create_item, create_list

MISSING = 987654321


def test_unknown_list_returns_404(client, user_a):
    response = client.get(f"/goals/{MISSING}", headers=user_a)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_unknown_list_operations_return_404(client, user_a):
    assert (
        client.patch(
            f"/goals/{MISSING}", json={"goal_name": "x"}, headers=user_a
        ).status_code
        == 404
    )
    assert client.delete(f"/goals/{MISSING}", headers=user_a).status_code == 404
    assert client.get(f"/goals/{MISSING}/tasks", headers=user_a).status_code == 404
    assert (
        client.post(
            f"/goals/{MISSING}/tasks", json={"task_name": "x"}, headers=user_a
        ).status_code
        == 404
    )


def test_unknown_item_returns_404(client, user_a):
    response = client.get(f"/tasks/{MISSING}", headers=user_a)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_unknown_item_operations_return_404(client, user_a):
    assert (
        client.patch(
            f"/tasks/{MISSING}", json={"status": "done"}, headers=user_a
        ).status_code
        == 404
    )
    assert client.delete(f"/tasks/{MISSING}", headers=user_a).status_code == 404


def test_exporting_an_unknown_list_returns_404(client, user_a):
    assert client.post(f"/goals/{MISSING}/exports", headers=user_a).status_code == 404


def test_unknown_export_job_returns_404(client, user_a):
    assert client.get(f"/exports/{MISSING}", headers=user_a).status_code == 404
    assert client.get(f"/exports/{MISSING}/download", headers=user_a).status_code == 404
    assert client.post(f"/exports/{MISSING}/retry", headers=user_a).status_code == 404


def test_deleting_a_list_twice_returns_404_the_second_time(client, user_a):
    goal_id = create_list(client, user_a, "Temporary")
    assert client.delete(f"/goals/{goal_id}", headers=user_a).status_code == 204
    second = client.delete(f"/goals/{goal_id}", headers=user_a)
    assert second.status_code == 404
    assert second.json()["error"]["code"] == "not_found"


def test_deleting_an_item_twice_returns_404_the_second_time(client, user_a):
    goal_id = create_list(client, user_a)
    task_id = create_item(client, user_a, goal_id)
    assert client.delete(f"/tasks/{task_id}", headers=user_a).status_code == 204
    assert client.delete(f"/tasks/{task_id}", headers=user_a).status_code == 404


def test_deleting_a_list_cascades_to_its_items(client, user_a):
    goal_id = create_list(client, user_a)
    task_id = create_item(client, user_a, goal_id)
    assert client.delete(f"/goals/{goal_id}", headers=user_a).status_code == 204
    assert client.get(f"/tasks/{task_id}", headers=user_a).status_code == 404

