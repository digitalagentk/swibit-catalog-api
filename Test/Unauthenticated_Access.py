"""Robustness case: Unauthenticated access.

Every protected operation must reject a caller that presents no token, a
malformed token, a token signed with the wrong key, or an expired token -
and it must do so with the same error envelope every time.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import jwt
import pytest

from Main.Authentication import JWT_ALGORITHM, JWT_SECRET

# (method, path, json_body) - bodies are valid so the only failure under test
# is the missing credential, not request validation.
PROTECTED = [
    ("get", "/auth/me", None),
    ("get", "/goals", None),
    ("post", "/goals", {"goal_name": "Private board"}),
    ("get", "/goals/1", None),
    ("patch", "/goals/1", {"goal_name": "Renamed"}),
    ("delete", "/goals/1", None),
    ("get", "/goals/1/tasks", None),
    ("post", "/goals/1/tasks", {"task_name": "An item"}),
    ("get", "/tasks/1", None),
    ("patch", "/tasks/1", {"status": "done"}),
    ("delete", "/tasks/1", None),
    ("get", "/exports", None),
    ("get", "/exports/1", None),
    ("get", "/exports/1/download", None),
    ("post", "/goals/1/exports", None),
    ("post", "/exports/1/retry", None),
]


@pytest.mark.parametrize("method,path,body", PROTECTED)
def test_protected_operations_reject_anonymous_callers(client, method, path, body):
    # httpx only accepts `json` on methods that take a body.
    kwargs = {"json": body} if body is not None else {}
    response = getattr(client, method)(path, **kwargs)
    assert response.status_code == 401, f"{method} {path} -> {response.status_code}"
    assert response.json()["error"]["code"] == "unauthenticated"


def test_malformed_token_is_rejected(client):
    response = client.get("/goals", headers={"Authorization": "Bearer not-a-real-jwt"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_token_signed_with_wrong_secret_is_rejected(client):
    forged = jwt.encode({"sub": "1"}, "attacker-secret", algorithm=JWT_ALGORITHM)
    response = client.get("/goals", headers={"Authorization": f"Bearer {forged}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_expired_token_is_rejected(client):
    past = datetime.now(timezone.utc) - timedelta(minutes=5)
    expired = jwt.encode(
        {"sub": "1", "exp": int(past.timestamp())},
        JWT_SECRET,
        algorithm=JWT_ALGORITHM,
    )
    response = client.get("/goals", headers={"Authorization": f"Bearer {expired}"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthenticated"


def test_public_endpoints_stay_open(client):
    assert client.get("/").status_code == 200
    assert client.get("/health").status_code == 200


def test_error_envelope_shape_is_stable(client):
    """Anonymous failures expose exactly one machine-readable code."""
    body = client.get("/goals").json()
    assert set(body) == {"error"}
    assert set(body["error"]) == {"code", "message"}

