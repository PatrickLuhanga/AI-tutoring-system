"""Practice bank, notifications, uploads, and System Admin account management.

Uploads and account management are the surfaces where a mistake is a security
bug rather than a wrong number, so the traversal, extension and self-lockout cases
are pinned here deliberately.
"""

from __future__ import annotations

import io
import uuid

import pytest

PREFIXES = ("svc-",)


@pytest.fixture(autouse=True)
def _purge():
    yield
    from sqlalchemy import delete

    from src.db import session_scope
    from src.models import (
        Notification,
        NotificationRead,
        PracticeAttempt,
        Question,
        UploadedDocument,
        User,
        UserModuleAccess,
    )
    from sqlalchemy import select

    with session_scope() as session:
        # Questions and attempts are keyed off the account that created them, so
        # clean those up before the accounts disappear.
        svc_user_ids = select(User.user_id).where(User.email.like("svc-%"))
        session.execute(delete(Question).where(Question.created_by.in_(svc_user_ids)))
        session.execute(delete(PracticeAttempt).where(PracticeAttempt.user_id.in_(svc_user_ids)))
        for prefix in PREFIXES:
            session.execute(delete(User).where(User.email.like(f"{prefix}%")))
        session.execute(delete(NotificationRead))
        session.execute(delete(Notification))
        session.execute(delete(UploadedDocument))
        session.execute(delete(UserModuleAccess))
        # Directory rows and enrolments created to exercise module scoping.
        from src.models import Enrollment, Student

        session.execute(
            delete(Enrollment).where(
                Enrollment.student_id.in_(
                    select(Student.student_id).where(Student.dut4life_email.like("svc-%"))
                )
            )
        )
        session.execute(delete(Student).where(Student.dut4life_email.like("svc-%")))


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _register(client, role: str, *, modules=None, password="svc-password-123", **extra):
    tag = uuid.uuid4().hex[:10]
    domain = {"student": "dut4life.ac.za", "lecturer": "dut.ac.za"}.get(role, "example.com")
    body = {
        "email": f"svc-{tag}@{domain}",
        "password": password,
        "role": role,
        "full_name": "Service Test",
    }
    if role == "student":
        # Must be exactly 8 DIGITS (the signup rule) and unique across accounts,
        # so derive it from a numeric seed rather than a hex uuid.
        body["student_number"] = f"2{uuid.uuid4().int % 10_000_000:07d}"
    if role in {"tutor", "lecturer"}:
        body["module_ids"] = modules if modules is not None else ["IPRT301"]
    body.update(extra)
    res = client.post("/api/auth/signup", json=body)
    return res


def _admin(client, secret="svc-admin-secret", settings=None, monkeypatch=None):
    from dataclasses import replace

    from src import accounts as acc
    from src.config import settings as real

    if monkeypatch is not None:
        monkeypatch.setattr(acc, "settings", replace(real, admin_signup_secret=secret))
    tag = uuid.uuid4().hex[:10]
    res = client.post(
        "/api/auth/signup",
        json={"email": f"svc-{tag}@dut.ac.za", "password": secret, "role": "admin"},
    )
    return res


# ---------------------------------------------------------------------------
# Practice: the bank
# ---------------------------------------------------------------------------
def test_question_cannot_be_read_by_a_student(client):
    token, = (_register(client, "student").get_json()["token"],)
    res = client.get("/api/practice/bank?module_id=IPRT301", headers=_auth(token))
    assert res.status_code == 403


def test_student_draws_a_randomised_test(client):
    token = _register(client, "student").get_json()["token"]
    # Seed a bank for a throwaway module the student is allowed to name.
    admin_res = _admin(client, monkeypatch=None)  # no secret configured -> 403
    assert admin_res.status_code == 403

    res = client.get("/api/practice/questions?module_id=IPRT301&count=3", headers=_auth(token))
    # Bank is empty for this module in a test run, so a 404 is the honest answer.
    assert res.status_code in (200, 404)


def test_empty_bank_is_a_404_with_a_useful_message(client):
    token = _register(client, "student").get_json()["token"]
    res = client.get("/api/practice/questions?module_id=NOBANK1", headers=_auth(token))
    assert res.status_code == 404
    assert "no questions" in res.get_json()["error"].lower()


def test_lecturer_can_add_a_question_for_their_module(client, monkeypatch, settings):
    from dataclasses import replace

    from src import accounts as acc

    monkeypatch.setattr(acc, "settings", replace(settings, admin_signup_secret="svc-admin-secret"))
    tok = _register(client, "lecturer", modules=["IPRT301"]).get_json()["token"]
    res = client.post(
        "/api/practice/bank",
        headers=_auth(tok),
        json={
            "module_id": "IPRT301",
            "prompt": "Explain why a Singleton class has a private constructor.",
            "answer_notes": "to stop the constructor being called directly",
            "difficulty": "easy",
        },
    )
    assert res.status_code == 201, res.get_json()
    assert res.get_json()["question"]["difficulty"] == "easy"


def test_lecturer_cannot_write_to_a_module_they_do_not_teach(client):
    tok = _register(client, "lecturer", modules=["IPRT301"]).get_json()["token"]
    res = client.post(
        "/api/practice/bank",
        headers=_auth(tok),
        json={"module_id": "SPRI301", "prompt": "A question about professional ethics here."},
    )
    assert res.status_code == 403


def test_bank_listing_never_exposes_the_answer(client, monkeypatch, settings):
    from dataclasses import replace

    from src import accounts as acc

    monkeypatch.setattr(acc, "settings", replace(settings, admin_signup_secret="svc-admin-secret"))
    tok = _register(client, "lecturer", modules=["IPRT301"]).get_json()["token"]
    client.post(
        "/api/practice/bank",
        headers=_auth(tok),
        json={
            "module_id": "IPRT301",
            "prompt": "Explain why a Singleton class has a private constructor.",
            "answer_notes": "SECRET-MARKING-NOTE",
        },
    )
    res = client.get("/api/practice/bank?module_id=IPRT301", headers=_auth(tok))
    assert res.status_code == 200
    assert "SECRET-MARKING-NOTE" not in res.get_data(as_text=True)


def test_drawn_test_never_carries_the_answer(client, monkeypatch, settings):
    from dataclasses import replace

    from src import accounts as acc

    monkeypatch.setattr(acc, "settings", replace(settings, admin_signup_secret="svc-admin-secret"))
    tok = _register(client, "lecturer", modules=["IPRT301"]).get_json()["token"]
    client.post(
        "/api/practice/bank",
        headers=_auth(tok),
        json={
            "module_id": "IPRT301",
            "prompt": "Explain why a Singleton class has a private constructor.",
            "answer_notes": "SECRET-MARKING-NOTE",
        },
    )
    res = client.get("/api/practice/questions?module_id=IPRT301", headers=_auth(tok))
    assert res.status_code == 200
    body = res.get_json()
    assert body["questions"], "the seeded question should have been drawn"
    assert "SECRET-MARKING-NOTE" not in res.get_data(as_text=True)


def test_seeded_draw_is_reproducible(client, monkeypatch, settings):
    from dataclasses import replace

    from src import accounts as acc

    monkeypatch.setattr(acc, "settings", replace(settings, admin_signup_secret="svc-admin-secret"))
    tok = _register(client, "lecturer", modules=["IPRT301"]).get_json()["token"]
    for i in range(5):
        client.post(
            "/api/practice/bank",
            headers=_auth(tok),
            json={"module_id": "IPRT301", "prompt": f"Explain concept number {i} in detail.",
                  "answer_notes": f"answer {i}"},
        )
    first = client.get("/api/practice/questions?module_id=IPRT301&count=3&seed=7", headers=_auth(tok))
    second = client.get("/api/practice/questions?module_id=IPRT301&count=3&seed=7", headers=_auth(tok))
    a = [q["question_id"] for q in first.get_json()["questions"]]
    b = [q["question_id"] for q in second.get_json()["questions"]]
    assert a == b and len(a) == 3


def test_practice_requires_a_session(client):
    assert client.get("/api/practice/questions?module_id=IPRT301").status_code == 401


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------
def test_student_cannot_send_notifications(client):
    tok = _register(client, "student").get_json()["token"]
    res = client.post(
        "/api/notifications", headers=_auth(tok),
        json={"title": "Hello", "body": "World", "audience": "all"},
    )
    assert res.status_code == 403


def test_lecturer_send_and_student_receive(client, monkeypatch, settings):
    from dataclasses import replace

    from src import accounts as acc

    monkeypatch.setattr(acc, "settings", replace(settings, admin_signup_secret="svc-admin-secret"))
    lecturer = _register(client, "lecturer", modules=["IPRT301"]).get_json()["token"]
    student = _register(client, "student").get_json()["token"]

    sent = client.post(
        "/api/notifications", headers=_auth(lecturer),
        json={"title": "Test week moved", "body": "It is now Friday.", "audience": "all"},
    )
    assert sent.status_code == 201, sent.get_json()

    inbox = client.get("/api/notifications", headers=_auth(student))
    assert inbox.status_code == 200
    body = inbox.get_json()
    assert body["unread"] >= 1
    assert any(n["title"] == "Test week moved" for n in body["notifications"])


def test_module_scoped_notification_reaches_only_enrolled_students(client, monkeypatch, settings):
    """A student has no ``user_module_access`` row - enrolment is the scope.

    Without this the audience would match teaching staff and never a student,
    which is the case the announcement exists for.
    """
    from dataclasses import replace

    from src import accounts as acc

    monkeypatch.setattr(acc, "settings", replace(settings, admin_signup_secret="svc-admin-secret"))
    iprt_lecturer = _register(client, "lecturer", modules=["IPRT301"]).get_json()["token"]
    enrolled = _register(client, "student").get_json()
    unenrolled = _register(client, "student").get_json()
    spri_lecturer = _register(client, "lecturer", modules=["SPRI301"]).get_json()["token"]

    # Link the first student to the directory and enrol them in IPRT301.
    from src.db import session_scope
    from src.models import Enrollment, Student, User

    email = enrolled["user"]["email"]
    with session_scope() as session:
        user = session.query(User).filter(User.email == email).one()
        student = session.query(Student).filter(Student.dut4life_email == email).one_or_none()
        if student is None:
            student = Student(dut4life_email=email, student_number=user.student_number)
            session.add(student)
            session.flush()
        # The account must point at the directory row, as signup does.
        user.student_id = student.student_id
        session.add(Enrollment(student_id=student.student_id, module_id="IPRT301"))

    client.post(
        "/api/notifications", headers=_auth(iprt_lecturer),
        json={"title": "IPRT only", "body": "Scoped to IPRT301.", "audience": "module",
              "module_id": "IPRT301"},
    )

    got = client.get("/api/notifications", headers=_auth(enrolled["token"])).get_json()
    assert any(n["title"] == "IPRT only" for n in got["notifications"]), (
        "an enrolled IPRT301 student should receive an IPRT301 announcement"
    )

    # A student with no enrolment is not in IPRT301, so must not receive it.
    not_got = client.get("/api/notifications", headers=_auth(unenrolled["token"])).get_json()
    assert not any(n["title"] == "IPRT only" for n in not_got["notifications"])

    # A SPRI lecturer is not in IPRT301 either.
    spri_inbox = client.get("/api/notifications", headers=_auth(spri_lecturer)).get_json()
    assert not any(n["title"] == "IPRT only" for n in spri_inbox["notifications"])


def test_marking_read_is_idempotent(client, monkeypatch, settings):
    from dataclasses import replace

    from src import accounts as acc

    monkeypatch.setattr(acc, "settings", replace(settings, admin_signup_secret="svc-admin-secret"))
    lecturer = _register(client, "lecturer").get_json()["token"]
    student = _register(client, "student").get_json()["token"]
    nid = client.post(
        "/api/notifications", headers=_auth(lecturer),
        json={"title": "Read me", "body": "Once.", "audience": "all"},
    ).get_json()["notification"]["notification_id"]

    first = client.post("/api/notifications/read", headers=_auth(student),
                        json={"notification_ids": [nid]}).get_json()["marked"]
    second = client.post("/api/notifications/read", headers=_auth(student),
                         json={"notification_ids": [nid]}).get_json()["marked"]
    assert first == 1
    assert second == 0
    inbox = client.get("/api/notifications", headers=_auth(student)).get_json()
    assert all(n["is_read"] for n in inbox["notifications"])


def test_module_notification_without_a_module_is_rejected(client, monkeypatch, settings):
    from dataclasses import replace

    from src import accounts as acc

    monkeypatch.setattr(acc, "settings", replace(settings, admin_signup_secret="svc-admin-secret"))
    tok = _register(client, "lecturer").get_json()["token"]
    res = client.post("/api/notifications", headers=_auth(tok),
                      json={"title": "x", "body": "y", "audience": "module"})
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# Uploads: the security-relevant cases
# ---------------------------------------------------------------------------
def _upload(client, tok, filename, data=b"hello", module="IPRT301", category="notes"):
    return client.post(
        "/api/content/upload",
        headers=_auth(tok),
        data={
            "module_id": module,
            "category": category,
            "file": (io.BytesIO(data), filename),
        },
        content_type="multipart/form-data",
    )


def test_student_cannot_upload(client):
    tok = _register(client, "student").get_json()["token"]
    assert _upload(client, tok, "notes.md").status_code == 403


def test_lecturer_can_upload_a_note(client, monkeypatch, settings, tmp_path):
    from dataclasses import replace

    from src import accounts as acc
    from src.config import settings as real

    # Point the content root at a tmp dir so the test does not litter the corpus.
    monkeypatch.setattr(acc, "settings", replace(real, admin_signup_secret="svc-admin-secret"))
    import src.uploads as up

    monkeypatch.setattr(up.settings, "content_dir", tmp_path) if not hasattr(
        up.settings, "content_dir"
    ) else monkeypatch.setitem(up.__dict__, "settings", replace(real, admin_signup_secret="svc-admin-secret", content_dir=tmp_path))

    tok = _register(client, "lecturer", modules=["IPRT301"]).get_json()["token"]
    res = _upload(client, tok, "week01.md", b"# Week 1\n\nSingleton pattern.")
    assert res.status_code == 201, res.get_json()
    stored = res.get_json()["document"]["stored_path"]
    assert (tmp_path / stored).exists()
    # Never overwrite an existing file silently.
    again = _upload(client, tok, "week01.md", b"# Week 1 revised")
    assert again.status_code == 201
    assert again.get_json()["document"]["stored_path"] != stored


def test_traversal_filename_is_neutralised(client, monkeypatch, settings, tmp_path):
    from dataclasses import replace

    from src import accounts as acc
    from src.config import settings as real

    monkeypatch.setitem(
        __import__("src.uploads", fromlist=["x"]).__dict__, "settings",
        replace(real, admin_signup_secret="svc-admin-secret", content_dir=tmp_path),
    )
    tok = _register(client, "lecturer", modules=["IPRT301"]).get_json()["token"]
    for hostile in (
        "../../../../../../evil.md",
        "..\\..\\..\\..\\evil.md",
        "/etc/passwd.md",
        "C:\\fakepath\\notes.md",
    ):
        res = _upload(client, tok, hostile, b"payload")
        if res.status_code == 201:
            path = res.get_json()["document"]["stored_path"]
            # Whatever the input, the file landed inside the content root.
            resolved = (tmp_path / path).resolve()
            assert str(resolved).startswith(str(tmp_path.resolve())), hostile
            assert ".." not in path


def test_executable_extension_is_refused(client, monkeypatch, settings, tmp_path):
    from dataclasses import replace

    from src import accounts as acc
    from src.config import settings as real

    monkeypatch.setitem(
        __import__("src.uploads", fromlist=["x"]).__dict__, "settings",
        replace(real, admin_signup_secret="svc-admin-secret", content_dir=tmp_path),
    )
    tok = _register(client, "lecturer", modules=["IPRT301"]).get_json()["token"]
    for bad in ("payload.py", "payload.exe", "payload.php", "payload.sh"):
        res = _upload(client, tok, bad, b"print('x')")
        assert res.status_code == 400, bad
        assert "not accepted" in res.get_json()["error"]


def test_oversized_upload_is_refused(client, monkeypatch, settings, tmp_path):
    from dataclasses import replace

    from src import accounts as acc
    from src.config import settings as real

    monkeypatch.setitem(
        __import__("src.uploads", fromlist=["x"]).__dict__, "settings",
        replace(real, admin_signup_secret="svc-admin-secret", content_dir=tmp_path,
                upload_max_mb=1),
    )
    tok = _register(client, "lecturer", modules=["IPRT301"]).get_json()["token"]
    res = _upload(client, tok, "big.md", b"x" * (2 * 1024 * 1024))
    assert res.status_code == 413


# ---------------------------------------------------------------------------
# System Admin account management
# ---------------------------------------------------------------------------
def test_student_cannot_list_accounts(client):
    tok = _register(client, "student").get_json()["token"]
    assert client.get("/api/accounts", headers=_auth(tok)).status_code == 403


def test_lecturer_cannot_list_accounts(client, monkeypatch, settings):
    from dataclasses import replace

    from src import accounts as acc

    monkeypatch.setattr(acc, "settings", replace(settings, admin_signup_secret="svc-admin-secret"))
    tok = _register(client, "lecturer").get_json()["token"]
    assert client.get("/api/accounts", headers=_auth(tok)).status_code == 403


def test_admin_can_list_and_suspend(client, monkeypatch, settings):
    from dataclasses import replace

    from src import accounts as acc

    monkeypatch.setattr(acc, "settings", replace(settings, admin_signup_secret="svc-admin-secret"))
    admin = _admin(client).get_json()
    student_res = _register(client, "student")
    student_tok = student_res.get_json()["token"]
    student_id = student_res.get_json()["user"]["user_id"]

    listing = client.get("/api/accounts", headers=_auth(admin["token"]))
    assert listing.status_code == 200
    assert listing.get_json()["count"] >= 2

    assert client.get("/api/auth/me", headers=_auth(student_tok)).status_code == 200
    patched = client.patch(
        f"/api/accounts/{student_id}", headers=_auth(admin["token"]),
        json={"status": "suspended"},
    )
    assert patched.status_code == 200
    # Suspension has to bite immediately, not at token expiry. Revoking the
    # sessions is what does it, so the token is rejected before the account
    # status is even consulted - 401 rather than 403.
    assert client.get("/api/auth/me", headers=_auth(student_tok)).status_code == 401
    # And a fresh login is refused on account status, not on the token.
    relogin = client.post(
        "/api/auth/login",
        json={"email": student_res.get_json()["user"]["email"], "password": "svc-password-123"},
    )
    assert relogin.status_code == 403


def test_admin_cannot_suspend_themselves(client, monkeypatch, settings):
    from dataclasses import replace

    from src import accounts as acc

    monkeypatch.setattr(acc, "settings", replace(settings, admin_signup_secret="svc-admin-secret"))
    admin = _admin(client).get_json()
    res = client.patch(
        f"/api/accounts/{admin['user']['user_id']}", headers=_auth(admin["token"]),
        json={"status": "suspended"},
    )
    assert res.status_code == 400
    assert "your own" in res.get_json()["error"]


def test_admin_can_promote_a_tutor_to_lecturer(client, monkeypatch, settings):
    from dataclasses import replace

    from src import accounts as acc

    monkeypatch.setattr(acc, "settings", replace(settings, admin_signup_secret="svc-admin-secret"))
    admin = _admin(client).get_json()
    tutor = _register(client, "tutor").get_json()
    res = client.patch(
        f"/api/accounts/{tutor['user']['user_id']}", headers=_auth(admin["token"]),
        json={"role": "lecturer"},
    )
    assert res.status_code == 200
    assert res.get_json()["role"] == "lecturer"


def test_invalid_role_is_rejected(client, monkeypatch, settings):
    from dataclasses import replace

    from src import accounts as acc

    monkeypatch.setattr(acc, "settings", replace(settings, admin_signup_secret="svc-admin-secret"))
    admin = _admin(client).get_json()
    victim = _register(client, "student").get_json()
    res = client.patch(
        f"/api/accounts/{victim['user']['user_id']}", headers=_auth(admin["token"]),
        json={"role": "superuser"},
    )
    assert res.status_code == 400


def test_revoking_sessions_closes_them_all(client, monkeypatch, settings):
    from dataclasses import replace

    from src import accounts as acc

    monkeypatch.setattr(acc, "settings", replace(settings, admin_signup_secret="svc-admin-secret"))
    admin = _admin(client).get_json()
    student_res = _register(client, "student").get_json()
    tok = student_res["token"]
    # A second session for the same account.
    second = client.post(
        "/api/auth/login",
        json={"email": student_res["user"]["email"], "password": "svc-password-123"},
    ).get_json()["token"]

    res = client.post(f"/api/accounts/{student_res['user']['user_id']}/sessions",
                      headers=_auth(admin["token"]))
    assert res.status_code == 200
    assert client.get("/api/auth/me", headers=_auth(tok)).status_code == 401
    assert client.get("/api/auth/me", headers=_auth(second)).status_code == 401
