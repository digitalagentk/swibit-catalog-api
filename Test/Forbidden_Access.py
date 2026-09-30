"""Robustness case: Forbidden access.

User B must never be able to read, update, or delete User A's list, nor add
or touch an item inside it - and no response may leak User A's data.
"""

from __future__ import annotations

import pytest

from Test.conftest import create_item, create_list


@pytest.fixture()
def alice_list(client, user_a):
    """A list containing one item, both owned by User A."""
    goal_id = create_list(client, user_a, "Alice's board")
    task_id = create_item(client, user_a, goal_id, "Alice's item")
    return goal_id, task_id


def test_user_b_cannot_read_user_a_list(client, user_b, alice_list):
    goal_id, _ = alice_list
    response = client.get(f"/goals/{goal_id}", headers=user_b)
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "forbidden"
    assert "Alice's board" not in response.text


def test_user_b_cannot_rename_user_a_list(client, user_b, user_a, alice_list):
    goal_id, _ = alice_list
    response = client.patch(
        f"/goals/{goal_id}", json={"goal_name": "Hijacked"}, headers=user_b
    )
    assert response.status_code == 403
    # The owner's name is unchanged.
    assert client.get(f"/goals/{goal_id}", headers=user_a).json()["goal_name"] == (
        "Alice's board"
    )


def test_user_b_cannot_delete_user_a_list(client, user_b, user_a, alice_list):
    goal_id, _ = alice_list
    response = client.delete(f"/goals/{goal_id}", headers=user_b)
    assert response.status_code == 403
    assert client.get(f"/goals/{goal_id}", headers=user_a).status_code == 200


def test_user_b_cannot_list_items_of_user_a_list(client, user_b, alice_list):
    goal_id, _ = alice_list
    response = client.get(f"/goals/{goal_id}/tasks", headers=user_b)
    assert response.status_code == 403
    assert "Alice's item" not in response.text


def test_user_b_cannot_add_item_to_user_a_list(client, user_b, user_a, alice_list):
    goal_id, _ = alice_list
    response = client.post(
        f"/goals/{goal_id}/tasks", json={"task_name": "Injected"}, headers=user_b
    )
    assert response.status_code == 403
    # Nothing was written.
    assert client.get(f"/goals/{goal_id}/tasks", headers=user_a).json()["total"] == 1


def test_user_b_cannot_read_update_or_delete_user_a_item(client, user_b, alice_list):
    _, task_id = alice_list
    assert client.get(f"/tasks/{task_id}", headers=user_b).status_code == 403
    assert (
        client.patch(
            f"/tasks/{task_id}", json={"status": "done"}, headers=user_b
        ).status_code
        == 403
    )
    assert client.delete(f"/tasks/{task_id}", headers=user_b).status_code == 403


def test_collections_never_include_other_users_rows(client, user_a, user_b, alice_list):
    """Both list and item collections are scoped to their owner."""
    alice_goal, alice_task = alice_list
    bob_goal = create_list(client, user_b, "Bob's board")
    bob_task = create_item(client, user_b, bob_goal, "Bob's item")

    bob_goals = client.get("/goals", headers=user_b).json()
    assert bob_goals["total"] == 1
    assert [row["goal_id"] for row in bob_goals["items"]] == [bob_goal]

    bob_items = client.get(f"/goals/{bob_goal}/tasks", headers=user_b).json()
    assert [row["task_id"] for row in bob_items["items"]] == [bob_task]

    alice_goals = client.get("/goals", headers=user_a).json()
    assert alice_goals["total"] == 1
    assert [row["goal_id"] for row in alice_goals["items"]] == [alice_goal]
    assert client.get(f"/tasks/{alice_task}", headers=user_a).status_code == 200

