"""Practice tests and the staff-authored question bank.

Routes
------
``GET  /api/practice/questions``         draw a randomised test for a module
``POST /api/practice/submit``             score a submitted test
``GET  /api/practice/attempts``           the caller's own attempt history
``GET  /api/practice/bank``               the module's bank (staff only)
``POST /api/practice/bank``               add a question (staff only)
``DELETE /api/practice/bank/<id>``        retire a question (staff only)
``POST /api/practice/generate``           staff-triggered generation from material

Module scope is taken from the caller's identity, never from the request, so a
student cannot ask for another module's bank by naming it.
"""

from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from ..auth import AuthError, require_student
from ..config import settings
from ..practice import (
    PracticeError,
    add_question,
    bank_size,
    delete_question,
    draw_test,
    first_party_material,
    generate_questions_from_text,
    get_question,
    update_question,
    list_questions,
    mark_attempt,
)

logger = logging.getLogger(__name__)

practice_bp = Blueprint("practice", __name__, url_prefix="/api/practice")

#: Staff who may write to the bank.
BANK_ROLES = {"tutor", "lecturer", "admin"}


def _require_staff(identity, module_id: str) -> None:
    """Staff may only touch the bank for a module they are assigned to."""
    if identity.role not in BANK_ROLES:
        raise AuthError("Tutor, lecturer or admin role required.", status_code=403)
    if identity.role == "admin":
        return
    wanted = str(module_id).strip().upper()
    allowed = {str(m).strip().upper() for m in (identity.module_ids or ())}
    if allowed and wanted not in allowed:
        raise AuthError(
            f"You are not assigned to {wanted}.", status_code=403
        )


@practice_bp.get("/questions")
def get_questions():
    """Draw a randomised test. ``?count=`` and ``?seed=`` are optional.

    ``seed`` makes the draw reproducible, which is how a lecturer can hand the
    same set to a class.
    """
    identity = require_student(request)
    module_id = (request.args.get("module_id") or "").strip().upper()
    if not module_id:
        return jsonify({"error": "module_id is required."}), 400
    try:
        count = int(request.args.get("count") or settings.practice_default_size)
    except ValueError:
        return jsonify({"error": "count must be a number."}), 400
    seed = request.args.get("seed")
    if seed is not None:
        try:
            seed = int(seed)
        except ValueError:
            return jsonify({"error": "seed must be a number."}), 400

    try:
        return jsonify(
            draw_test(module_id=module_id, count=count, seed=seed)
        ), 200
    except PracticeError as exc:
        return jsonify({"error": exc.message}), exc.status_code


@practice_bp.post("/submit")
def post_submit():
    """Body::

        {"module_id": "IPRT301", "question_ids": [1, 2],
         "answers": {"1": "a singleton", "2": "..."}}
    """
    identity = require_student(request)
    payload = request.get_json(silent=True) or {}
    module_id = (payload.get("module_id") or "").strip().upper()
    if not module_id:
        return jsonify({"error": "module_id is required."}), 400
    try:
        result = mark_attempt(
            module_id=module_id,
            question_ids=payload.get("question_ids") or [],
            answers=payload.get("answers") or {},
            student_id=identity.student_id,
            user_id=identity.user_id,
        )
    except PracticeError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    return jsonify(result), 200


@practice_bp.get("/bank")
def get_bank():
    """The question bank for a module, staff only.

    Answers **are** included here, unlike on ``GET /api/practice/questions`` which
    serves students. The route is already gated by ``_require_staff`` for the
    caller's modules, and a lecturer cannot responsibly approve an answer they
    are not allowed to read - approving is what turns a draft into a marking key.
    """
    identity = require_student(request)
    module_id = (request.args.get("module_id") or "").strip().upper()
    if not module_id:
        return jsonify({"error": "module_id is required."}), 400
    _require_staff(identity, module_id)
    return jsonify(
        {
            "module_id": module_id,
            "size": bank_size(module_id),
            "questions": list_questions(module_id, include_answers=True),
        }
    ), 200


@practice_bp.post("/bank")
def post_bank():
    """Add one question. Staff only, and only for a module they teach.

    A hand-written question defaults to ``answer_source='authored'``, which is
    the only kind :func:`src.practice.mark_attempt` will score against. Passing
    ``answer_source`` explicitly is how an AI draft is created knowingly as a
    reference rather than a key.
    """
    identity = require_student(request)
    payload = request.get_json(silent=True) or {}
    module_id = (payload.get("module_id") or "").strip().upper()
    if not module_id:
        return jsonify({"error": "module_id is required."}), 400
    _require_staff(identity, module_id)
    try:
        row = add_question(
            module_id=module_id,
            prompt=payload.get("prompt"),
            answer_notes=payload.get("answer_notes"),
            answer_source=payload.get("answer_source"),
            difficulty=payload.get("difficulty"),
            origin=payload.get("origin") or "authored",
            source_label=payload.get("source_label"),
            created_by=identity.user_id,
        )
    except PracticeError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    return jsonify({"question": row}), 201


@practice_bp.patch("/bank/<int:question_id>")
def patch_bank(question_id: int):
    """Edit a question, or approve a drafted answer. Staff only.

    This is the route that makes the marking model usable rather than merely
    safe. Every answer in a seeded bank is ``generated``, and a generated answer
    is never auto-marked - correctly, because the bank has no human behind it. The
    only way a seeded question ever starts being scored is a lecturer reading it,
    correcting it, and saying so.

    ``answer_source='authored'`` is that decision. It is deliberately not implied
    by editing ``answer_notes``: someone tidying a typo in a draft should not
    silently promote it to a marking key.
    """
    identity = require_student(request)
    if identity.role not in BANK_ROLES:
        raise AuthError("Tutor, lecturer or admin role required.", status_code=403)

    payload = request.get_json(silent=True) or {}
    row = get_question(question_id)
    if row is None:
        return jsonify({"error": "Question not found."}), 404
    _require_staff(identity, row["module_id"])

    # Only fields actually present are touched, so a partial update cannot blank
    # a question by omitting a key.
    fields = {key: payload[key] for key in ("prompt", "answer_notes", "difficulty") if key in payload}
    if "answer_source" in payload:
        fields["answer_source"] = payload.get("answer_source")
    if "is_active" in payload:
        fields["is_active"] = bool(payload.get("is_active"))

    if not fields:
        return jsonify({"error": "Nothing to update."}), 400

    try:
        updated = update_question(question_id, **fields)
    except PracticeError as exc:
        return jsonify({"error": exc.message}), exc.status_code
    if updated is None:
        return jsonify({"error": "Question not found."}), 404
    return jsonify({"question": updated}), 200


@practice_bp.delete("/bank/<int:question_id>")
def delete_bank(question_id: int):
    """Retire a question. Staff only."""
    identity = require_student(request)
    if identity.role not in BANK_ROLES:
        raise AuthError("Tutor, lecturer or admin role required.", status_code=403)
    if not delete_question(question_id):
        return jsonify({"error": "Question not found."}), 404
    return jsonify({"status": "retired", "question_id": question_id}), 200


@practice_bp.post("/generate-from-corpus")
def post_generate_from_corpus():
    """Generate questions from a module's own ingested material.

    Body:: ``{"module_id": "IPRT301", "count": 5, "difficulty": "medium"}``

    This is the on-request path a lecturer uses to top a module's bank up: no
    pasting, and the material is whatever is already ingested. Third-party
    textbooks are excluded, because a question grounded in a commercial book's
    wording is not your module's material and would not match how the course is
    examined.
    """
    identity = require_student(request)
    payload = request.get_json(silent=True) or {}
    module_id = (payload.get("module_id") or "").strip().upper()
    if not module_id:
        return jsonify({"error": "module_id is required."}), 400
    _require_staff(identity, module_id)

    try:
        count = int(payload.get("count") or 5)
    except (TypeError, ValueError):
        return jsonify({"error": "count must be a number."}), 400

    material = first_party_material(module_id)
    if len(material) < 200:
        return jsonify(
            {
                "error": f"There is not enough ingested material for {module_id} to "
                "generate from. Upload or ingest notes first, or paste the text instead.",
            }
        ), 409

    try:
        created = generate_questions_from_text(
            module_id=module_id,
            text=material,
            count=max(1, min(count, 10)),
            created_by=identity.user_id,
            difficulty=payload.get("difficulty") or "medium",
        )
    except PracticeError as exc:
        return jsonify({"error": exc.message}), exc.status_code

    return jsonify(
        {
            "created": created,
            "count": len(created),
            "source": "corpus",
            "material_chars": len(material),
        }
    ), 201


@practice_bp.post("/generate")
def post_generate():
    """Staff-triggered generation from supplied material.

    Body::

        {"module_id": "IPRT301", "text": "...", "count": 3}

    Slow by nature on CPU, so it is a staff action rather than something a
    student triggers mid-test.
    """
    identity = require_student(request)
    payload = request.get_json(silent=True) or {}
    module_id = (payload.get("module_id") or "").strip().upper()
    if not module_id:
        return jsonify({"error": "module_id is required."}), 400
    _require_staff(identity, module_id)
    try:
        created = generate_questions_from_text(
            module_id=module_id,
            text=payload.get("text") or "",
            count=int(payload.get("count") or 3),
            created_by=identity.user_id,
            difficulty=payload.get("difficulty") or "medium",
        )
    except (PracticeError, ValueError) as exc:
        if isinstance(exc, ValueError):
            return jsonify({"error": "count must be a number."}), 400
        return jsonify({"error": exc.message}), exc.status_code
    return jsonify({"created": created, "count": len(created)}), 201
