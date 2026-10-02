"""Signup, login and session rules for the four account types.

These cover the policy the specification fixes: which email domain each role may
register with, the student-number requirement, that a System Admin cannot be
self-created, and that sessions are revocable.
"""

from __future__ import annotations

import uuid

import pytest

# `client` is the shared app fixture from tests/conftest.py.

#: The shared suite runs against the live development database, so these tests
#: must not assume a clean slate and must not leave accounts behind. Every
#: request uses a per-test unique address, and `_purge_accounts` removes exactly
#: the rows this module created once the test is over.
PASSWORD = "correct-horse-battery"
PREFIXES = ("acct-test-", "acct-lect-", "acct-other-", "acct-admin-")


@pytest.fixture(autouse=True)
def _purge_accounts():
    """Delete any account created during the test, sessions included."""
    yield
    from sqlalchemy import delete

    from src.db import session_scope
    from src.models import User

    with session_scope() as session:
        for prefix in PREFIXES:
            session.execute(delete(User).where(User.email.like(f"{prefix}%")))


def _email_for(role: str) -> str:
    tag = uuid.uuid4().hex[:10]
    if role == "student":
        return f"acct-test-{tag}@dut4life.ac.za"
    if role == "lecturer":
        return f"acct-lect-{tag}@dut.ac.za"
    return f"acct-other-{tag}@example.com"


def _signup(client, **overrides):
    """POST /api/auth/signup with a unique address and sensible defaults."""
    role = str(overrides.pop("role", "student"))
    body = {
        "email": _email_for(role),
        "password": PASSWORD,
        "role": role,
        "full_name": "Test User",
        "student_number": "22000000",
    }
    if role in {"tutor", "lecturer"}:
        body["module_ids"] = ["IPRT301"]
    body.update(overrides)
    return client.post("/api/auth/signup", json=body)


def _register(client, **overrides) -> tuple[str, str]:
    """Signup that is expected to succeed. Returns ``(token, email)``."""
    res = _signup(client, **overrides)
    assert res.status_code == 201, res.get_json()
    body = res.get_json()
    return body["token"], body["user"]["email"]


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Email domain policy
# ---------------------------------------------------------------------------
def test_student_requires_dut4life_domain(client):
    res = _signup(client, email="someone@gmail.com")
    assert res.status_code == 403
    assert "dut4life.ac.za" in res.get_json()["error"]


def test_student_accepts_dut4life(client):
    assert _signup(client).status_code == 201


def test_student_number_must_be_eight_digits(client):
    res = _signup(client, student_number="123")
    assert res.status_code == 400
    assert "8 digits" in res.get_json()["error"]


def test_student_number_is_required(client):
    res = _signup(client, student_number=None)
    assert res.status_code == 400
    assert "student number" in res.get_json()["error"].lower()


def test_tutor_is_not_domain_restricted(client):
    res = _signup(client, role="tutor", email=_email_for("tutor"))
    assert res.status_code == 201
    assert res.get_json()["user"]["modules"] == ["IPRT301"]


def test_lecturer_requires_dut_domain(client):
    res = _signup(client, role="lecturer", email=f"acct-lect-x{uuid.uuid4().hex[:8]}@other.ac.za")
    assert res.status_code == 403
    assert "dut.ac.za" in res.get_json()["error"]


def test_lecturer_accepts_dut_domain(client):
    token, _ = _register(client, role="lecturer", email=_email_for("lecturer"))
    assert token
    assert client.get("/api/auth/me", headers=_auth(token)).get_json()["user"]["role"] == "lecturer"


def test_lecturer_subdomain_is_allowed(client):
    tag = uuid.uuid4().hex[:8]
    res = _signup(client, role="lecturer", email=f"acct-lect-{tag}@cs.dut.ac.za")
    assert res.status_code == 201


def test_lookalike_domain_is_rejected(client):
    """`dut.ac.za.evil.com` must not pass a naive substring check."""
    tag = uuid.uuid4().hex[:8]
    res = _signup(
        client, role="lecturer", email=f"acct-lect-{tag}@dut.ac.za.evil.com"
    )
    assert res.status_code == 403


# ---------------------------------------------------------------------------
# Admin cannot self-register
# ---------------------------------------------------------------------------
def test_admin_cannot_self_signup(client):
    res = _signup(client, role="admin", email="root@dut.ac.za", student_number=None)
    assert res.status_code == 403
    assert "System Admin" in res.get_json()["error"]


# ---------------------------------------------------------------------------
# Module scope
# ---------------------------------------------------------------------------
def test_staff_must_choose_a_module(client):
    res = _signup(client, role="tutor", email=_email_for("tutor"), module_ids=[])
    assert res.status_code == 400
    assert "module" in res.get_json()["error"].lower()


def test_unknown_module_is_rejected_not_ignored(client):
    """A typo must fail loudly rather than create an account that sees nothing."""
    res = _signup(
        client, role="tutor", email=_email_for("tutor"), module_ids=["NOPE999"]
    )
    assert res.status_code == 400
    assert "NOPE999" in res.get_json()["error"]


def test_students_ignore_any_module_they_claim(client):
    """Student scope comes from enrolment, never from the signup form."""
    res = _signup(client, module_ids=["IPRT301"])
    assert res.status_code == 201
    assert res.get_json()["user"]["modules"] == []


# ---------------------------------------------------------------------------
# Passwords
# ---------------------------------------------------------------------------
def test_short_password_rejected(client):
    res = _signup(client, password="abc")
    assert res.status_code == 400


def test_password_is_hashed_not_stored(client):
    from sqlalchemy import select

    from src.db import session_scope
    from src.models import User

    _, email = _register(client)
    with session_scope() as session:
        user = session.execute(select(User).where(User.email == email)).scalar_one()
        assert PASSWORD not in user.password_hash
        assert user.password_hash.startswith("scrypt:")


def test_duplicate_email_conflicts(client):
    _, email = _register(client)
    res = _signup(client, email=email)
    assert res.status_code == 409


def test_duplicate_student_number_conflicts(client):
    _register(client)
    res = _signup(client, email=_email_for("student"))
    assert res.status_code == 409


# ---------------------------------------------------------------------------
# Login and sessions
# ---------------------------------------------------------------------------
def test_login_returns_token_and_user(client):
    _, email = _register(client)
    res = client.post("/api/auth/login", json={"email": email, "password": PASSWORD})
    assert res.status_code == 200
    body = res.get_json()
    assert body["token"]
    assert body["user"]["role"] == "student"


def test_login_is_case_insensitive_on_email(client):
    _, email = _register(client)
    res = client.post("/api/auth/login", json={"email": email.upper(), "password": PASSWORD})
    assert res.status_code == 200


def test_wrong_password_is_401(client):
    _, email = _register(client)
    assert client.post("/api/auth/login", json={"email": email, "password": "nope"}).status_code == 401


def test_unknown_email_gives_same_message_as_wrong_password(client):
    """Must not be usable to enumerate registered accounts."""
    _, email = _register(client)
    unknown = client.post(
        "/api/auth/login", json={"email": _email_for("student"), "password": "nope"}
    ).get_json()["error"]
    wrong = client.post(
        "/api/auth/login", json={"email": email, "password": "nope"}
    ).get_json()["error"]
    assert unknown == wrong


def test_me_requires_a_token(client):
    assert client.get("/api/auth/me").status_code == 401


def test_me_rejects_a_garbage_token(client):
    assert client.get("/api/auth/me", headers=_auth("nope")).status_code == 401


def test_me_returns_the_signed_in_account(client):
    token, email = _register(client)
    res = client.get("/api/auth/me", headers=_auth(token))
    assert res.status_code == 200
    assert res.get_json()["user"]["email"] == email


def test_logout_revokes_the_token_immediately(client):
    """The reason sessions are server-side rather than self-contained tokens."""
    token, _ = _register(client)
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 200
    assert client.post("/api/auth/logout", headers=_auth(token)).status_code == 200
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401


def test_logout_only_affects_that_session(client):
    token, email = _register(client)
    second = client.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    ).get_json()["token"]
    client.post("/api/auth/logout", headers=_auth(token))
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401
    assert client.get("/api/auth/me", headers=_auth(second)).status_code == 200


def test_logout_all_closes_every_session(client):
    token, email = _register(client)
    second = client.post(
        "/api/auth/login", json={"email": email, "password": PASSWORD}
    ).get_json()["token"]
    res = client.post("/api/auth/logout-all", headers=_auth(token))
    assert res.get_json()["sessions_closed"] >= 2
    assert client.get("/api/auth/me", headers=_auth(second)).status_code == 401


def test_expired_session_is_refused(client):
    from datetime import datetime, timedelta, timezone

    from sqlalchemy import select

    from src.db import session_scope
    from src.models import User, UserSession

    token, email = _register(client)
    # Scope to *this* account's session. Other tests in the module leave live
    # sessions behind, so picking an arbitrary row expires the wrong one.
    with session_scope() as session:
        user_id = session.execute(
            select(User.user_id).where(User.email == email)
        ).scalar_one()
        row = session.execute(
            select(UserSession).where(UserSession.user_id == user_id)
        ).scalar_one()
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 401


def test_suspended_account_cannot_use_a_live_token(client):
    from sqlalchemy import select

    from src.db import session_scope
    from src.models import User

    token, email = _register(client)
    with session_scope() as session:
        user = session.execute(select(User).where(User.email == email)).scalar_one()
        user.status = "suspended"
    assert client.get("/api/auth/me", headers=_auth(token)).status_code == 403


# ---------------------------------------------------------------------------
# System Admin bootstrap guard
# ---------------------------------------------------------------------------
ADMIN_SECRET = "unit-test-admin-secret"


@pytest.fixture()
def admin_secret(monkeypatch):
    """Point ``src.accounts`` at a Settings copy carrying ``secret``.

    ``Settings`` is a frozen dataclass, so the field cannot be assigned in place;
    the module-level import in ``src.accounts`` is what gets swapped.
    """
    from dataclasses import replace

    from src import accounts as acc
    from src.config import settings as real

    def _apply(secret: str) -> None:
        monkeypatch.setattr(acc, "settings", replace(real, admin_signup_secret=secret))

    return _apply


def _admin_email(tag: str | None = None) -> str:
    return f"acct-admin-{tag or uuid.uuid4().hex[:10]}@dut.ac.za"


def _admin_signup(client, password=ADMIN_SECRET, email=None):
    return client.post(
        "/api/auth/signup",
        json={
            "email": email or _admin_email(),
            "password": password,
            "role": "admin",
            "full_name": "Sys Admin",
        },
    )


def test_admin_signup_is_refused_when_no_secret_configured(client, admin_secret):
    """Fails closed: an unset secret means nobody can bootstrap an admin."""
    admin_secret("")
    res = _admin_signup(client)
    assert res.status_code == 403
    assert "not enabled" in res.get_json()["error"]


def test_admin_signup_with_the_secret_succeeds(client, admin_secret):
    admin_secret(ADMIN_SECRET)
    res = _admin_signup(client)
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["user"]["role"] == "admin"
    assert res.get_json()["user"]["is_staff"] is True


def test_admin_signup_with_a_wrong_secret_is_refused(client, admin_secret):
    admin_secret(ADMIN_SECRET)
    res = _admin_signup(client, password="guess")
    assert res.status_code == 403
    assert "incorrect" in res.get_json()["error"].lower()


def test_admin_secret_does_not_leak_on_success(client, admin_secret):
    admin_secret(ADMIN_SECRET)
    body = _admin_signup(client).get_data(as_text=True)
    assert ADMIN_SECRET not in body


def test_repeated_bad_admin_guesses_are_throttled(client, admin_secret):
    """A shared secret is only one guess wide, so guesses get rate limited."""
    from src import accounts as acc

    admin_secret(ADMIN_SECRET)
    with acc._admin_lock:
        acc._admin_attempts.clear()

    statuses = [_admin_signup(client, password=f"guess-{i}").status_code for i in range(5)]
    assert statuses == [403] * 5

    # Sixth attempt is locked out, not merely refused.
    assert _admin_signup(client, password=ADMIN_SECRET).status_code == 429
    with acc._admin_lock:
        acc._admin_attempts.clear()


def test_a_correct_guess_after_failures_is_allowed(client, admin_secret):
    """Throttling must lock out guessing, not lock out a legitimate operator."""
    from src import accounts as acc

    admin_secret(ADMIN_SECRET)
    with acc._admin_lock:
        acc._admin_attempts.clear()
    _admin_signup(client, password="typo")
    assert _admin_signup(client).status_code == 201


def test_admin_needs_no_module_scope(client, admin_secret):
    """Admins are system-wide, so the module requirement does not apply."""
    admin_secret(ADMIN_SECRET)
    res = _admin_signup(client)
    assert res.status_code == 201
    assert res.get_json()["user"]["modules"] == []


def test_ordinary_roles_are_unaffected_by_the_admin_secret(client, admin_secret):
    admin_secret(ADMIN_SECRET)
    assert _signup(client).status_code == 201
    assert _signup(client, role="tutor", email=_email_for("tutor")).status_code == 201


# ---------------------------------------------------------------------------
# Public signup policy
# ---------------------------------------------------------------------------
def test_config_exposes_the_policy(client):
    body = client.get("/api/auth/config").get_json()
    assert body["allow_self_signup"] is True
    assert body["student_email_domain"] == "dut4life.ac.za"
    assert body["lecturer_email_domain"] == "dut.ac.za"


def test_config_never_reveals_whether_the_admin_secret_is_set(client, admin_secret):
    """Reporting "admin signup enabled" would confirm the secret exists."""
    admin_secret(ADMIN_SECRET)
    body = client.get("/api/auth/config").get_data(as_text=True)
    assert "allow_admin_signup" not in body
    assert ADMIN_SECRET not in body


def test_config_leaks_no_secrets(client):
    body = client.get("/api/auth/config").get_data(as_text=True)
    assert "admin_api_key" not in body
    assert "admin_signup_secret" not in body
