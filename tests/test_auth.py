"""Authentication and authorization must fail closed.

These were previously verified by a throwaway script, so no later change could
notice the admin gate being weakened. The specific regression worth guarding is
the one that already happened: a placeholder key shipped in ``.env.example``, was
copied into a local ``.env`` verbatim, and kept authenticating while only a
warning was logged.
"""

from __future__ import annotations

import types

import pytest

from src.auth import KNOWN_WEAK_ADMIN_KEYS
from src.config import settings

ADMIN_PATHS = (
    "/api/admin/analytics",
    "/api/admin/llm-config",
    "/api/admin/ollama-models",
)

STRONG_KEY = "t8Kq2vN4pR7xW3mZ9cL5yB1dF6gH0jA"


@pytest.fixture
def admin_key(monkeypatch):
    """Override the admin key on a mutable stand-in for the frozen settings.

    ``settings`` is a frozen dataclass, so the module reference is swapped for a
    copy rather than mutated. ``require_admin`` reads ``settings.admin_api_key`` at
    call time, so this is the seam the test needs.
    """
    import src.auth as auth_module

    patchable = types.SimpleNamespace(**{f: getattr(settings, f) for f in dir(settings)
                                         if not f.startswith("_")})
    monkeypatch.setattr(auth_module, "settings", patchable)
    return patchable


# ---------------------------------------------------------------------------
# The admin gate
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("path", ADMIN_PATHS)
def test_admin_routes_reject_a_request_with_no_key(client, admin_key, path):
    admin_key.admin_api_key = STRONG_KEY
    admin_key.admin_allow_weak_key = False
    assert client.get(path).status_code == 401


@pytest.mark.parametrize("path", ADMIN_PATHS)
def test_admin_routes_reject_a_wrong_key(client, admin_key, path):
    admin_key.admin_api_key = STRONG_KEY
    admin_key.admin_allow_weak_key = False
    response = client.get(path, headers={"X-Admin-Key": "definitely-not-the-key"})
    assert response.status_code == 401


@pytest.mark.parametrize("placeholder", sorted(KNOWN_WEAK_ADMIN_KEYS - {""}))
def test_admin_routes_reject_a_known_placeholder_key(client, admin_key, placeholder):
    """The failure that motivated this file: the example key worked."""
    admin_key.admin_api_key = placeholder
    admin_key.admin_allow_weak_key = False
    response = client.get("/api/admin/analytics", headers={"X-Admin-Key": placeholder})
    assert response.status_code == 503, (
        f"the shipped placeholder {placeholder!r} still authenticates"
    )


def test_placeholder_rejection_can_be_overridden_for_local_work(client, admin_key):
    admin_key.admin_api_key = "local-dev-admin-key"
    admin_key.admin_allow_weak_key = True
    response = client.get(
        "/api/admin/analytics", headers={"X-Admin-Key": "local-dev-admin-key"}
    )
    assert response.status_code == 200


def test_configured_strong_key_is_accepted(client, admin_key):
    admin_key.admin_api_key = STRONG_KEY
    admin_key.admin_allow_weak_key = False
    ok = client.get("/api/admin/analytics", headers={"X-Admin-Key": STRONG_KEY})
    bad = client.get("/api/admin/analytics", headers={"X-Admin-Key": STRONG_KEY[:-1] + "B"})
    assert ok.status_code == 200
    assert bad.status_code == 401


def test_empty_key_disables_the_admin_api_rather_than_allowing_anything(client, admin_key):
    """Fails closed: no key means no admin API, not an open one."""
    admin_key.admin_api_key = ""
    admin_key.admin_allow_weak_key = False
    response = client.get("/api/admin/analytics", headers={"X-Admin-Key": ""})
    assert response.status_code == 503


def test_the_shipped_example_config_contains_no_working_key():
    """A working key in .env.example is a working key in every clone."""
    from pathlib import Path

    example = Path(__file__).resolve().parent.parent / ".env.example"
    if not example.is_file():
        pytest.skip("no .env.example in this checkout")
    seen = False
    for line in example.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("ADMIN_API_KEY"):
            seen = True
            value = stripped.split("=", 1)[1].strip() if "=" in stripped else ""
            assert not value, f".env.example ships a working admin key: {value!r}"
    assert seen, ".env.example no longer mentions ADMIN_API_KEY at all"


def test_the_active_config_is_not_using_a_placeholder_key():
    """Either a real key, or disabled - never a shipped placeholder in use."""
    key = (settings.admin_api_key or "").strip()
    if not key:
        return  # disabled, which is the safe state
    assert key.casefold() not in KNOWN_WEAK_ADMIN_KEYS or settings.admin_allow_weak_key, (
        "the running configuration is using a known placeholder admin key"
    )


# ---------------------------------------------------------------------------
# Tutor routes: scoped, not open
# ---------------------------------------------------------------------------
TUTOR = {"X-User-Email": "tutor@dut4life.ac.za", "X-User-Role": "tutor"}


def test_tutor_queue_requires_a_tutor_role(client):
    assert client.get("/api/tutor/questions?module_id=IPRT301").status_code == 403


def test_a_student_cannot_read_the_tutor_queue(client):
    response = client.get(
        "/api/tutor/questions?module_id=IPRT301",
        headers={"X-User-Email": "student@dut4life.ac.za", "X-User-Role": "student"},
    )
    assert response.status_code == 403


def test_tutor_queue_is_scoped_to_the_requested_module(client):
    response = client.get("/api/tutor/questions?module_id=IPRT301", headers=TUTOR)
    assert response.status_code == 200
    body = response.get_json()
    for row in body.get("questions", []):
        assert row.get("module_id") == "IPRT301", (
            "the tutor queue returned a question from another module"
        )


def test_a_tutor_cannot_read_another_modules_queue_by_asking(client):
    """The scope filter must hold even when the caller names another module."""
    response = client.get("/api/tutor/questions?module_id=PBDV301", headers=TUTOR)
    assert response.status_code in (200, 403, 404)
    if response.status_code == 200:
        assert response.get_json().get("questions", []) == [], (
            "a tutor scoped to IPRT301 was shown PBDV301 questions"
        )


def test_response_reports_the_callers_own_scope(client):
    response = client.get("/api/tutor/questions?module_id=IPRT301", headers=TUTOR)
    assert response.status_code == 200
    assert "scope" in response.get_json()


# ---------------------------------------------------------------------------
# Known gap, pinned so it cannot change silently
# ---------------------------------------------------------------------------
def test_a_header_authenticated_tutor_has_no_module_scope(client):
    """A real, currently-unfixed gap: the tutor role cannot reach the queue.

    ``resolve_tutor_scope`` needs ``identity.student_id`` to look up
    ``tutor_assignments``, but a header-derived identity always has
    ``student_id=None`` because DUT4life is not wired. So every
    ``X-User-Role: tutor`` caller gets an empty scope and an empty queue, and
    ``tutor_assignments`` is unreachable in practice.

    The Human Adjustment Cycle is therefore only operable by a caller asserting
    ``X-User-Role: admin``. This is safe in the sense that nothing leaks, but it
    is not the scoping the design intends, and the paper should not describe tutor
    role-based access as working until an identity provider sets ``student_id``.

    Fixing it means deciding how a tutor's modules are established (DUT4life
    subject, an enrollment table, or an explicit assignment endpoint) - an identity
    decision, not a patch. Until then this test asserts the safe behaviour: no
    questions, rather than all of them.
    """
    response = client.get("/api/tutor/questions", headers=TUTOR)
    assert response.status_code == 200
    body = response.get_json()
    assert body["count"] == 0, (
        "a tutor with no resolved scope is now seeing questions; if this is "
        "intended, update the note above and add the scoping that justifies it"
    )


def test_the_admin_key_does_not_open_the_tutor_queue(client, admin_key):
    """The tutor queue is scoped by identity, not by the admin key.

    Deliberate separation: ``/api/admin/*`` is gated by ADMIN_API_KEY, the tutor
    queue by the caller's role. Sharing the key across both would widen the blast
    radius of one leaked secret.
    """
    admin_key.admin_api_key = STRONG_KEY
    admin_key.admin_allow_weak_key = False
    response = client.get("/api/tutor/questions", headers={"X-Admin-Key": STRONG_KEY})
    assert response.status_code == 403
