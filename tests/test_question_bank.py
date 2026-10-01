"""Tests for exporting and loading the seeded question bank.

The fixture in ``exam_papers/question_bank.json`` is what makes a fresh clone
have a working Practice view, so the properties that matter are: loading it is
idempotent, it never destroys what a lecturer has written, and it refuses the
destructive path without explicit consent.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy import func, select

from src.db import session_scope
from src.models import Question
from src.seed_question_bank import (
    MANAGED_MARKERS,
    SCHEMA_VERSION,
    check_bank,
    export_bank,
    load_bank,
)


@pytest.fixture
def fixture(tmp_path):
    """A small stand-in for the real fixture."""
    payload = {
        "schema_version": SCHEMA_VERSION,
        "source": "test",
        "papers": [],
        "questions": [
            {
                "module_id": "IPRT301",
                "prompt": "zb-test-alpha: compute the mean of three integers",
                "answer_notes": "return a + b;",
                "difficulty": "medium",
                "origin": "past_paper",
                "source_label": "2024 - Midyear Main - IPRT301 (p3) (OCR - verify)",
            },
            {
                "module_id": "IPRT301",
                "prompt": "zb-test-beta: name two properties of a balanced binary tree",
                "answer_notes": None,
                "difficulty": "easy",
                "origin": "past_paper",
                "source_label": "2024 - Midyear Main - IPRT301 (p4) (OCR - verify)",
            },
        ],
    }
    path = tmp_path / "question_bank.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


#: Exactly the prompts this module writes. The purge matches on these and on
#: nothing else.
#:
#: It deliberately does *not* sweep on ``source_label LIKE '%(OCR - verify)%'``.
#: An earlier version did, and it silently deleted the whole seeded practice
#: bank - all 192 real rows - because a test run shares the live development
#: database with everything else.
#:
#: The prompts are deliberately not ones a real bank would contain: the
#: extracted papers really do contain "Write the method calculate() in the
#: class Arithmetic.", so a plausible-looking fixture prompt collides with a
#: real row and the purge then deletes it.
_TEST_PROMPTS = (
    "zb-test-alpha: compute the mean of three integers",
    "zb-test-beta: name two properties of a balanced binary tree",
    "zb-test-gamma: a lecturer's own question",
    "zb-test-delta: an obsolete question a lecturer retired",
)


@pytest.fixture(scope="module", autouse=True)
def _preserve_bank():
    """Snapshot the real question bank and put it back afterwards.

    Two tests here call ``--reset``, which empties the ``questions`` table. The
    suite runs against the live development database, so without this, running
    pytest would delete the seeded practice bank and anyone starting the app
    afterwards would find Practice empty with no indication why.
    """
    from src.seed_question_bank import CARRIED

    with session_scope() as session:
        saved = [
            {key: getattr(row, key) for key in CARRIED}
            for row in session.execute(select(Question)).scalars()
        ]

    yield

    with session_scope() as session:
        session.query(Question).delete(synchronize_session=False)
    for row in saved:
        with session_scope() as session:
            session.add(Question(**row, is_active=True))


@pytest.fixture(autouse=True)
def _clean():
    """Remove the rows these tests create, before and after."""
    def purge():
        with session_scope() as session:
            session.query(Question).filter(Question.prompt.in_(_TEST_PROMPTS)).delete(
                synchronize_session=False
            )
    purge()
    yield
    purge()


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------
def test_load_bank_inserts_the_fixture(fixture):
    inserted, skipped = load_bank(fixture)
    assert inserted == 2
    assert skipped == 0


def test_load_bank_is_idempotent(fixture):
    """A second run must not double the bank - setup scripts run every time."""
    load_bank(fixture)
    inserted, skipped = load_bank(fixture)
    assert inserted == 0
    assert skipped == 2

    with session_scope() as session:
        total = session.scalar(
            select(func.count())
            .select_from(Question)
            .where(Question.prompt == "zb-test-alpha: compute the mean of three integers")
        )
    assert total == 1


def test_load_bank_matches_on_normalised_text(fixture):
    """A re-indented or differently-cased prompt is the same question."""
    load_bank(fixture)
    payload = json.loads(fixture.read_text(encoding="utf-8"))
    payload["questions"] = [
        {**payload["questions"][0], "prompt": "  ZB-TEST-ALPHA:   COMPUTE THE MEAN   OF THREE INTEGERS  "}
    ]
    fixture.write_text(json.dumps(payload), encoding="utf-8")

    inserted, skipped = load_bank(fixture)
    assert inserted == 0
    assert skipped == 1


def test_load_bank_rejects_an_unknown_schema(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"schema_version": 99, "questions": [{}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="schema"):
        load_bank(path)


def test_load_bank_rejects_an_empty_fixture(tmp_path):
    path = tmp_path / "empty.json"
    path.write_text(json.dumps({"schema_version": SCHEMA_VERSION, "questions": []}), encoding="utf-8")
    with pytest.raises(ValueError, match="no questions"):
        load_bank(path)


def test_load_bank_reports_a_missing_fixture(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_bank(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# The destructive path
# ---------------------------------------------------------------------------
def test_reset_refuses_without_confirmation(fixture):
    with pytest.raises(ValueError, match="--yes"):
        load_bank(fixture, reset=True, confirm_reset=False)


def test_reset_clears_and_reloads(fixture):
    load_bank(fixture)
    inserted, _ = load_bank(fixture, reset=True, confirm_reset=True)
    assert inserted == 2


def test_reset_would_take_a_lecturers_questions_too(fixture):
    """Pinned deliberately: this is the footgun the refusal exists for.

    There is no way to tell a seeder row from a lecturer-written one that looks
    the same, so --reset is documented as destroying both.
    """
    from src.practice import add_question

    add_question(
        module_id="IPRT301",
        prompt="zb-test-gamma: a lecturer's own question",
        answer_notes=None,
        difficulty="medium",
        origin="authored",
        source_label=None,
        created_by=None,
    )
    with session_scope() as session:
        before = session.scalar(
            select(func.count()).select_from(Question).where(Question.prompt == "zb-test-gamma: a lecturer's own question")
        )
    assert before == 1

    load_bank(fixture, reset=True, confirm_reset=True)

    with session_scope() as session:
        after = session.scalar(
            select(func.count()).select_from(Question).where(Question.prompt == "zb-test-gamma: a lecturer's own question")
        )
    assert after == 0


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------
def test_export_leaves_lecturer_authored_questions_out(tmp_path, fixture):
    """They belong to whoever wrote them, and their user_id resolves nowhere."""
    from src.practice import add_question

    load_bank(fixture)
    add_question(
        module_id="IPRT301",
        prompt="zb-test-gamma: a lecturer's own question",
        answer_notes=None,
        difficulty="medium",
        origin="authored",
        source_label=None,
        created_by=None,
    )

    out = tmp_path / "out.json"
    payload = export_bank(out)

    prompts = [q["prompt"] for q in payload["questions"]]
    assert "zb-test-gamma: a lecturer's own question" not in prompts
    assert "zb-test-alpha: compute the mean of three integers" in prompts


def test_export_includes_authored_questions_when_asked(tmp_path, fixture):
    from src.practice import add_question

    load_bank(fixture)
    add_question(
        module_id="IPRT301",
        prompt="zb-test-gamma: a lecturer's own question",
        answer_notes=None,
        difficulty="medium",
        origin="authored",
        source_label=None,
        created_by=None,
    )
    out = tmp_path / "out.json"
    payload = export_bank(out, only_managed=False)
    assert "zb-test-gamma: a lecturer's own question" in [q["prompt"] for q in payload["questions"]]


def test_export_round_trips_the_fields_the_bank_needs(tmp_path, fixture):
    load_bank(fixture)
    out = tmp_path / "out.json"
    payload = export_bank(out)
    entry = next(q for q in payload["questions"] if "zb-test-alpha" in q["prompt"])
    assert entry["answer_notes"] == "return a + b;"
    assert entry["origin"] == "past_paper"
    assert entry["difficulty"] == "medium"
    assert entry["module_id"] == "IPRT301"


def test_export_does_not_carry_database_specific_columns(tmp_path, fixture):
    load_bank(fixture)
    out = tmp_path / "out.json"
    payload = export_bank(out)
    for entry in payload["questions"]:
        assert set(entry) == {
            "module_id",
            "prompt",
            "answer_notes",
            "answer_source",
            "difficulty",
            "origin",
            "source_label",
        }


# ---------------------------------------------------------------------------
# Check
# ---------------------------------------------------------------------------
def test_check_reports_no_drift_after_a_load(fixture):
    load_bank(fixture)
    missing, extra = check_bank(fixture)
    assert missing == []
    assert extra == []


def test_check_reports_what_is_missing(fixture):
    missing, extra = check_bank(fixture)
    assert len(missing) == 2
    assert extra == []


def test_check_flags_a_seeded_row_the_fixture_does_not_have(fixture):
    load_bank(fixture)
    with session_scope() as session:
        row = Question(
            module_id="IPRT301",
            prompt="zb-test-delta: an obsolete question a lecturer retired",
            difficulty="medium",
            origin="past_paper",
            source_label=f"2023 - Midyear Main - IPRT301 (p2) {MANAGED_MARKERS[0]}",
            is_active=True,
        )
        session.add(row)

    missing, extra = check_bank(fixture)
    assert missing == []
    assert len(extra) == 1


# ---------------------------------------------------------------------------
# The real fixture
# ---------------------------------------------------------------------------
def test_the_committed_fixture_loads_and_is_well_formed():
    """The fixture is the thing a clone depends on, so it is checked, not trusted."""
    from src.seed_question_bank import DEFAULT_FIXTURE

    if not DEFAULT_FIXTURE.exists():
        pytest.skip("exam_papers/question_bank.json is not present")

    payload = json.loads(DEFAULT_FIXTURE.read_text(encoding="utf-8"))
    assert payload["schema_version"] == SCHEMA_VERSION
    questions = payload["questions"]
    assert len(questions) > 50, "the bank should be substantial, not a stub"

    for entry in questions:
        assert entry["module_id"], entry
        assert entry["prompt"].strip(), entry
        assert entry["origin"] in {"authored", "past_paper", "generated"}, entry
        assert entry["difficulty"] in {"easy", "medium", "hard"}, entry

    modules = {q["module_id"] for q in questions}
    # PBDV301 has no papers, so its questions must be flagged as generated.
    pbdv = [q for q in questions if q["module_id"] == "PBDV301"]
    assert pbdv, "PBDV301 should have generated questions"
    assert all(q["origin"] == "generated" for q in pbdv)
    assert all("AI-generated" in (q["source_label"] or "") for q in pbdv)

    # Everything else must be traceable to a paper.
    past = [q for q in questions if q["origin"] == "past_paper"]
    assert past
    for entry in past:
        assert "(OCR - verify)" in (entry["source_label"] or ""), entry
        assert " - " in entry["source_label"], entry


def test_the_committed_papers_are_present():
    from src.seed_question_bank import DEFAULT_FIXTURE

    papers = sorted(DEFAULT_FIXTURE.parent.glob("*.pdf"))
    if not papers:
        pytest.skip("exam_papers/*.pdf are not present")
    assert len(papers) == 12