"""Session gating on the routes that used to trust a self-declared identity.

``X-User-Email`` was accepted from anyone, which is fine for curl against a local
database and not fine for anything else. These pin the new behaviour: a bearer
session is honoured, its absence is a 401, and a revoked session stays revoked.

The ``dev`` header form is deliberately still accepted, because the project's
own docs and scripts rely on it. ``AUTH_MODE=session`` is what closes it.
"""

from __future__ import annotations

import uuid

import pytest

from src.unanswered import TutorScope, resolve_tutor_scope

PREFIXES = ("gate-",)


@pytest.fixture(autouse=True)
def _purge():
    yield
    from sqlalchemy import delete

    from src.db import session_scope
    from src.models import User

    with session_scope() as session:
        for prefix in PREFIXES:
            session.execute(delete(User).where(User.email.like(f"{prefix}%")))


def _account(role: str, **overrides) -> dict:
    tag = uuid.uuid4().hex[:10]
    domain = {"student": "dut4life.ac.za", "lecturer": "dut.ac.za"}.get(role, "example.com")
    body = {
        "email": f"gate-{tag}@{domain}",
        "password": "gate-test-password",
        "role": role,
        "full_name": "Gate Test",
    }
    if role == "student":
        body["student_number"] = "22114455"
    if role in {"tutor", "lecturer"}:
        body["module_ids"] = ["IPRT301"]
    body.update(overrides)
    return body


def _register(client, role: str, **overrides) -> tuple[str, str]:
    res = client.post("/api/auth/signup", json=_account(role, **overrides))
    assert res.status_code == 201, res.get_json()
    body = res.get_json()
    return body["token"], body["user"]["email"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Chat and feedback
# ---------------------------------------------------------------------------
def test_chat_without_any_identity_is_401(client):
    assert client.post("/api/chat", json={"message": "hi", "module_id": "IPRT301"}).status_code == 401


def test_chat_with_a_revoked_session_is_401(client):
    """Revocation has to reach the routes, not just /api/auth/me."""
    token, _ = _register(client, "student")
    assert client.post("/api/auth/logout", headers=_auth(token)).status_code == 200
    res = client.post(
        "/api/chat",
        json={"message": "hi", "module_id": "IPRT301"},
        headers=_auth(token),
    )
    assert res.status_code == 401


def test_chat_with_a_garbage_token_is_401(client):
    res = client.post(
        "/api/chat", json={"message": "hi", "module_id": "IPRT301"}, headers=_auth("nope")
    )
    assert res.status_code == 401


def test_feedback_without_any_identity_is_401(client):
    res = client.post(
        "/api/feedback",
        json={
            "session_id": "s1",
            "message_id": "m1",
            "module_id": "IPRT301",
            "rating": 1,
        },
    )
    assert res.status_code == 401


def test_dev_header_still_works_in_dev_mode(client, settings, monkeypatch):
    """The docs' curl examples must keep working when AUTH_MODE=dev."""
    from dataclasses import replace

    import src.auth as auth_module

    auth_module.settings = replace(settings, auth_mode="dev")
    try:
        res = client.post(
            "/api/chat",
            json={"message": "hi", "module_id": "IPRT301"},
            headers={"X-User-Email": "22000000@dut4life.ac.za", "X-User-Role": "student"},
        )
    finally:
        auth_module.settings = settings
    # Reaches the workflow rather than being refused as unauthenticated. The
    # exact status depends on the LLM being available, which is not what this
    # test is about.
    assert res.status_code != 401


def test_dev_header_is_refused_in_session_mode(client, settings, monkeypatch):
    """The deployment default: a header cannot stand in for a real login."""
    from dataclasses import replace

    import src.auth as auth_module

    auth_module.settings = replace(settings, auth_mode="session")
    try:
        res = client.post(
            "/api/chat",
            json={"message": "hi", "module_id": "IPRT301"},
            headers={"X-User-Email": "22000000@dut4life.ac.za", "X-User-Role": "admin"},
        )
    finally:
        auth_module.settings = settings
    assert res.status_code == 401


# ---------------------------------------------------------------------------
# Staff module scope
# ---------------------------------------------------------------------------
def test_student_session_carries_no_module_scope(client):
    from src.auth import identity_from_session

    token, email = _register(client, "student")
    with client.application.test_request_context(
        "/", headers=_auth(token)
    ):
        identity = identity_from_session(_req())
    assert identity.role == "student"
    assert identity.source == "session"
    assert identity.module_ids == ()


def test_lecturer_session_resolves_its_modules(client):
    """A lecturer is scoped like a tutor, from the account not the header."""
    from src.auth import identity_from_session

    token, _ = _register(client, "lecturer", module_ids=["IPRT301", "PBDV301"])
    with client.application.test_request_context("/", headers=_auth(token)):
        identity = identity_from_session(_req())
        scope = resolve_tutor_scope(identity)
    assert identity.role == "lecturer"
    assert sorted(scope.module_ids) == ["IPRT301", "PBDV301"]
    assert scope.is_admin is False
    assert scope.allows("IPRT301") is True
    assert scope.allows("SPRI301") is False


def test_admin_session_sees_every_module(client):
    from src.accounts import AccountError  # noqa: F401
    from src.auth import identity_from_session

    from src import accounts as acc
    from dataclasses import replace
    from src.config import settings as real

    secret = "gate-admin-secret"
    original = real.admin_signup_secret
    import src.accounts as acc_mod

    acc_mod.settings = replace(real, admin_signup_secret=secret)
    try:
        token, _ = _register(client, "admin", password=secret)
    finally:
        acc_mod.settings = real
    with client.application.test_request_context("/", headers=_auth(token)):
        identity = identity_from_session(_req())
        scope = resolve_tutor_scope(identity)
    assert scope.is_admin is True
    assert scope.allows("ANY301") is True


def _req():
    from flask import request

    return request


# ---------------------------------------------------------------------------
# Tutor routes
# ---------------------------------------------------------------------------
def test_student_cannot_reach_the_tutor_queue(client):
    token, _ = _register(client, "student")
    res = client.get("/api/tutor/questions", headers=_auth(token))
    assert res.status_code == 403


def test_tutor_queue_requires_a_role(client, settings):
    """No identity at all is Forbidden in dev mode, per the existing contract."""
    from dataclasses import replace

    import src.auth as auth_module

    auth_module.settings = replace(settings, auth_mode="dev")
    try:
        assert client.get("/api/tutor/questions").status_code == 403
    finally:
        auth_module.settings = settings


def test_lecturer_can_reach_the_tutor_queue(client):
    token, _ = _register(client, "lecturer")
    res = client.get("/api/tutor/questions", headers=_auth(token))
    assert res.status_code == 200
    assert sorted(res.get_json()["scope"]["modules"]) == ["IPRT301"]
