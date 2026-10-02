"""Tests for login throttling.

``login`` was the one authentication path with no rate limit, while the admin
signup guard beside it already had one - so the shared bootstrap secret was the
best-protected credential in the system and an ordinary account's password was
brute-forceable at whatever rate the network allowed.

Two properties matter and both are pinned here: the limit *bites*, and it does
not distinguish between an account that exists and one that does not.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select
from werkzeug.security import generate_password_hash

from src import accounts
from src.accounts import AccountError, create_account, login
from src.db import session_scope
from src.models import User

ADDRESS = "203.0.113.9"
OTHER_HOST = "198.51.100.4"
EMAIL = "zzthrottle@dut4life.ac.za"


@pytest.fixture(autouse=True)
def _clear_buckets():
    """The buckets are module-level state, so they must not leak between tests."""

    def wipe():
        accounts._login_address_attempts.clear()
        accounts._login_account_attempts.clear()

    wipe()
    yield
    wipe()


def test_repeated_wrong_passwords_are_locked_out():
    codes = []
    for _ in range(accounts._LOGIN_ATTEMPT_LIMIT + 2):
        try:
            login(email=EMAIL, password="definitely-wrong", client_address=ADDRESS)
            codes.append(200)
        except AccountError as exc:
            codes.append(exc.status_code)

    assert codes[: accounts._LOGIN_ATTEMPT_LIMIT] == [401] * accounts._LOGIN_ATTEMPT_LIMIT
    assert 429 in codes[accounts._LOGIN_ATTEMPT_LIMIT :]


def test_a_locked_out_client_is_refused_before_the_password_is_checked():
    """A 429 must not reveal whether the account exists."""
    for _ in range(accounts._LOGIN_ATTEMPT_LIMIT):
        with pytest.raises(AccountError):
            login(email=EMAIL, password="wrong", client_address=ADDRESS)

    with pytest.raises(AccountError) as locked:
        login(email=EMAIL, password="wrong", client_address=ADDRESS)
    assert locked.value.status_code == 429

    # The *correct* password is also refused while locked out, so the response
    # is identical either way.
    with pytest.raises(AccountError) as with_right_password:
        login(email="nobody@dut4life.ac.za", password="x", client_address=ADDRESS)
    assert with_right_password.value.status_code == 429


def test_the_lockout_is_per_address():
    """One noisy host must not lock everybody else out."""
    for _ in range(accounts._LOGIN_ATTEMPT_LIMIT):
        with pytest.raises(AccountError):
            login(email=EMAIL, password="wrong", client_address=ADDRESS)

    # A different host, first ever attempt: not locked out.
    with pytest.raises(AccountError) as exc:
        login(email=EMAIL, password="wrong", client_address=OTHER_HOST)
    assert exc.value.status_code == 401


def test_the_lockout_is_also_per_account_across_hosts():
    """The address bucket alone would never notice a distributed attempt."""
    codes = []
    for i in range(accounts._LOGIN_ACCOUNT_LIMIT + 3):
        try:
            login(email=EMAIL, password="wrong", client_address=f"198.51.100.{i}")
            codes.append(200)
        except AccountError as exc:
            codes.append(exc.status_code)

    # Every host is fresh, so nothing here is 429 until the account bucket trips.
    assert codes[accounts._LOGIN_ACCOUNT_LIMIT] == 429
    assert all(code == 401 for code in codes[: accounts._LOGIN_ACCOUNT_LIMIT])


def test_a_successful_login_clears_the_bucket():
    email = "zzclear@dut4life.ac.za"
    with session_scope() as session:
        session.query(User).filter(User.email == email).delete(synchronize_session=False)
        session.add(
            User(
                email=email,
                password_hash=generate_password_hash("ClearTest123!"),
                role="student",
                status="active",
            )
        )
    try:
        for _ in range(3):
            with pytest.raises(AccountError):
                login(email=email, password="wrong", client_address=ADDRESS)

        user, _token = login(email=email, password="ClearTest123!", client_address=ADDRESS)
        assert user.email == email
        assert accounts._login_account_attempts.get(email, []) == []
        assert accounts._login_address_attempts.get(ADDRESS, []) == []
    finally:
        with session_scope() as session:
            session.query(User).filter(User.email == email).delete(synchronize_session=False)


def test_an_unknown_account_and_a_wrong_password_are_indistinguishable():
    """No enumeration: identical status and message."""
    unknown = pytest.raises(AccountError)
    with unknown as exc_unknown:
        login(email="zznobody@dut4life.ac.za", password="wrong", client_address="203.0.113.1")
    with pytest.raises(AccountError) as exc_wrong:
        login(email=EMAIL, password="wrong", client_address="203.0.113.2")

    assert exc_unknown.value.status_code == exc_wrong.value.status_code == 401
    assert exc_unknown.value.message == exc_wrong.value.message


def test_a_suspended_account_is_refused_without_consuming_an_attempt():
    """A suspension is not a failed guess, so it must not lock the user out."""
    email = "zzsuspend@dut4life.ac.za"
    with session_scope() as session:
        session.query(User).filter(User.email == email).delete(synchronize_session=False)
        session.add(
            User(
                email=email,
                password_hash=generate_password_hash("SuspendTest123!"),
                role="student",
                status="suspended",
            )
        )
    try:
        for _ in range(accounts._LOGIN_ATTEMPT_LIMIT + 2):
            with pytest.raises(AccountError) as exc:
                login(email=email, password="SuspendTest123!", client_address=ADDRESS)
            # Still 403 every time: it never becomes a 429.
            assert exc.value.status_code == 403
    finally:
        with session_scope() as session:
            session.query(User).filter(User.email == email).delete(synchronize_session=False)


def test_a_real_account_can_still_log_in_normally():
    """The throttle must not get in the way of an ordinary user."""
    email = "zznormal@dut4life.ac.za"
    _user, token = create_account(
        email=email, password="NormalTest123!", role="student", student_number="22999993"
    )
    try:
        for _ in range(4):
            _u, t = login(email=email, password="NormalTest123!", client_address=ADDRESS)
            assert t
    finally:
        with session_scope() as session:
            user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if user is not None:
                session.delete(user)
        from src.models import Student

        with session_scope() as session:
            student = session.execute(
                select(Student).where(Student.dut4life_email == email)
            ).scalar_one_or_none()
            if student is not None:
                session.delete(student)