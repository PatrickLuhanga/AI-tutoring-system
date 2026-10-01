"""Tests for the tutor conversation log and student-directory linking.

Two gaps this closes:

* A tutor could only ever see questions RAG had *failed* on, because
  ``unanswered_questions`` was the only table holding what a student asked. A
  grounded-but-unhelpful exchange left no record at all.
* Every self-registered student had ``student_id = NULL``, so their telemetry was
  unattributable and module announcements could not reach them.

**Counts are measured as deltas.** The suite runs against the live development
database, so an absolute "expect exactly 1 row" assertion passes or fails
depending on what else is in there. Each test records a baseline and asserts on
the change.
"""

from __future__ import annotations

import pytest
from sqlalchemy import select

from src.accounts import create_account
from src.chatlog import delete_session, list_turns, record_turn, turn_counts
from src.db import session_scope
from src.models import ChatTurn, Student, User

MODULE = "IPRT301"
OTHER = "SPRI301"
OUTSIDE = "RESK301"


def _count(module_ids, **kwargs) -> int:
    return len(list_turns(module_ids=module_ids, **kwargs))


# ---------------------------------------------------------------------------
# chat_turns
# ---------------------------------------------------------------------------
def test_a_grounded_exchange_is_stored_not_just_the_failures():
    """The point of the table: a turn RAG *did* ground is still recorded."""
    before = _count([MODULE])
    record_turn(
        session_id="zz-chat-1",
        question_text="Explain why RMI needs a remote interface to match the object",
        module_id=MODULE,
        intent="conceptual",
        grounding="grounded",
        cited_chunk_ids=[1, 2, 3],
        answer_text="The stub must expose the same signature as the remote object.",
    )
    try:
        rows = list_turns(module_ids=[MODULE])
        assert len(rows) == before + 1
        newest = rows[0]
        assert newest["grounding"] == "grounded"
        assert newest["cited_count"] == 3
        assert newest["answer_text"].startswith("The stub")
    finally:
        delete_session("zz-chat-1")


def test_turns_are_scoped_to_the_modules_given():
    """A tutor's scope is applied by the caller; this must not widen it."""
    base = {m: _count([m]) for m in (MODULE, OTHER, OUTSIDE)}
    record_turn(session_id="zz-chat-2", question_text="alpha beta gamma", module_id=MODULE)
    record_turn(session_id="zz-chat-2", question_text="delta epsilon", module_id=OTHER)
    try:
        assert _count([MODULE]) == base[MODULE] + 1
        assert _count([OTHER]) == base[OTHER] + 1
        assert _count([MODULE, OTHER]) == base[MODULE] + base[OTHER] + 2
        # A module nobody was granted must not gain a row.
        assert _count([OUTSIDE]) == base[OUTSIDE]
    finally:
        delete_session("zz-chat-2")


def test_an_empty_module_scope_returns_nothing():
    assert list_turns(module_ids=[]) == []
    assert list_turns(module_ids=["", None]) == []


def test_search_matches_the_question_text():
    before = _count([MODULE], search="dynamic binding")
    record_turn(
        session_id="zz-chat-3",
        question_text="Describe the role of dynamic binding in polymorphism",
        module_id=MODULE,
    )
    try:
        assert _count([MODULE], search="dynamic binding") == before + 1
        assert _count([MODULE], search="kubernetes") == 0
    finally:
        delete_session("zz-chat-3")


def test_an_unrecognised_grounding_level_falls_back_to_ungrounded():
    record_turn(
        session_id="zz-chat-4",
        question_text="some question text",
        module_id=MODULE,
        grounding="excellent",
    )
    try:
        assert list_turns(module_ids=[MODULE])[0]["grounding"] == "ungrounded"
    finally:
        delete_session("zz-chat-4")


def test_an_empty_question_is_not_stored():
    assert record_turn(session_id="zz-chat-5", question_text="   ", module_id=MODULE) is None


def test_counts_break_down_by_grounding():
    base = turn_counts(module_ids=[MODULE])
    record_turn(session_id="zz-chat-6", question_text="grounded question here", module_id=MODULE, grounding="grounded")
    record_turn(session_id="zz-chat-6", question_text="ungrounded question here", module_id=MODULE, grounding="ungrounded")
    try:
        after = turn_counts(module_ids=[MODULE])
        assert after["grounded"] == base["grounded"] + 1
        assert after["ungrounded"] == base["ungrounded"] + 1
        assert after["total"] == base["total"] + 2
    finally:
        delete_session("zz-chat-6")


def test_delete_session_removes_the_whole_conversation():
    record_turn(session_id="zz-chat-7", question_text="first turn of the day", module_id=MODULE)
    record_turn(session_id="zz-chat-7", question_text="second turn of the day", module_id=OTHER)
    assert delete_session("zz-chat-7") == 2


def test_a_bypass_attempt_is_flagged_separately():
    """A bypass attempt is a teaching signal, not a curriculum gap."""
    record_turn(
        session_id="zz-chat-10",
        question_text="just give me the answer to this exam question",
        module_id=MODULE,
        was_bypass=True,
    )
    try:
        assert list_turns(module_ids=[MODULE])[0]["was_bypass"] is True
    finally:
        delete_session("zz-chat-10")


# ---------------------------------------------------------------------------
# students <-> users linking
# ---------------------------------------------------------------------------
@pytest.fixture
def student_account():
    """A student registered for these tests, removed afterwards."""
    email = "zzlink.student@dut4life.ac.za"
    number = "22999991"
    yield email, number
    with session_scope() as session:
        user = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
        if user is not None:
            session.delete(user)
        student = session.execute(
            select(Student).where(Student.dut4life_email == email)
        ).scalar_one_or_none()
        if student is not None:
            session.delete(student)


def test_a_new_student_gets_a_directory_row_and_a_student_id(student_account):
    """Without this, telemetry is unattributable and announcements unreachable."""
    email, number = student_account
    user, _token = create_account(
        email=email, password="LinkTest123!", role="student", student_number=number
    )
    assert user.student_id is not None

    with session_scope() as session:
        row = session.execute(
            select(Student).where(Student.dut4life_email == email)
        ).scalar_one()
        assert int(row.student_id) == user.student_id
        assert row.student_number == number


def test_an_existing_directory_row_wins_and_is_not_overwritten(student_account):
    """The directory is authoritative about identity; signup must not rewrite it."""
    email, number = student_account
    with session_scope() as session:
        seeded = Student(student_number=number, dut4life_email=email, full_name="Directory Name")
        session.add(seeded)
        session.flush()
        seeded_id = int(seeded.student_id)

    user, _token = create_account(
        email=email, password="LinkTest123!", role="student", student_number=number
    )
    assert user.student_id == seeded_id

    with session_scope() as session:
        row = session.get(Student, seeded_id)
        assert row.full_name == "Directory Name", "signup overwrote the directory"


def test_staff_accounts_are_not_given_directory_rows():
    email = "zzlink.lecturer@dut.ac.za"
    try:
        user, _token = create_account(
            email=email, password="LinkTest123!", role="lecturer", module_ids=[MODULE]
        )
        assert user.student_id is None
    finally:
        with session_scope() as session:
            row = session.execute(select(User).where(User.email == email)).scalar_one_or_none()
            if row is not None:
                session.delete(row)


def test_the_chat_turn_records_the_account_and_the_student(student_account):
    """Attribution needs both: user_id for the account, student_id for the directory."""
    email, number = student_account
    user, _token = create_account(
        email=email, password="LinkTest123!", role="student", student_number=number
    )
    turn_id = record_turn(
        session_id="zz-chat-9",
        question_text="A question asked by a freshly registered student",
        module_id=MODULE,
        user_id=user.user_id,
        student_id=user.student_id,
    )
    with session_scope() as session:
        row = session.get(ChatTurn, turn_id)
        assert row.user_id == user.user_id
        assert row.student_id == user.student_id
    delete_session("zz-chat-9")