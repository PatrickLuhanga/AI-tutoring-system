"""Admin-only routes for system oversight and the Dynamic LLM Router.

All routes require the shared admin key (``X-Admin-Key``). API keys are stored
encrypted and never echoed back - ``GET`` returns only whether a key is set and
its last four characters.
"""

from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import distinct, func, select


from ..config import settings
from ..db import session_scope
from ..llm_router import LLMConfigService, LLMRouter
from ..auth import require_admin
from ..models import (
    FEEDBACK_REASON_TAGS,
    HintFeedback,
    Module,
    TelemetryLog,
    UnansweredQuestion,
)

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")

#: Human labels for the thumbs-down reason tags (section 12.1).
_REASON_LABELS = {
    "too_confusing": "Too confusing / still stuck",
    "too_long_or_too_short": "Too long / too short",
    "gave_away_answer": "Gave away the answer",
    "incorrect_answer": "Incorrect answer or code",
}


def _router() -> LLMRouter:
    return current_app.extensions["llm_router"]


def _service() -> LLMConfigService:
    return current_app.extensions["llm_config_service"]


@admin_bp.get("/llm-config")
def get_llm_config():
    """Return the active provider/model (never the API key itself)."""
    require_admin(request)
    router = _router()
    return jsonify(router.describe_active()), 200


@admin_bp.post("/llm-config")
def update_llm_config():
    """Switch provider and/or rotate the cloud API key.

    Example bodies::

        {"provider": "local", "local_model": "qwen3:4b"}
        {"provider": "cloud", "cloud_provider": "openai",
         "cloud_base_url": "https://api.openai.com/v1",
         "cloud_model": "gpt-4o-mini", "api_key": "sk-..."}
    """
    identity = require_admin(request)
    payload = request.get_json(silent=True) or {}
    payload.setdefault("updated_by", identity.email or "admin")

    try:
        record = _service().upsert_active(payload)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    described = _service().describe(record)
    logger.info(
        "LLM config updated by %s -> provider=%s model=%s",
        payload.get("updated_by"),
        described["provider"],
        described["effective"]["model"],
    )
    return jsonify(described), 200


@admin_bp.get("/ollama-models")
def list_ollama_models():
    """Ping the local Ollama API and return downloaded models for a dropdown."""
    require_admin(request)
    base_url = request.args.get("base_url") or settings.ollama_base_url
    models = _router().list_ollama_models(base_url)
    return (
        jsonify(
            {
                "base_url": base_url.rstrip("/"),
                "count": len(models),
                "models": models,
            }
        ),
        200,
    )


@admin_bp.get("/analytics")
def get_analytics():
    """Aggregate telemetry for the Admin dashboard (section 12.2).

    Reports the system-wide view: satisfaction, thumbs-down failure reasons,
    per-module satisfaction, mean latency, and how much of the grounding came
    from faculty-approved material rather than commercial textbooks. Also
    surfaces the ungrounded-question queue depth, since that is the clearest
    single indicator of curriculum coverage.
    """
    require_admin(request)
    try:
        payload = _build_analytics()
    except Exception as exc:  # noqa: BLE001 - a data-tier outage must not 500 the panel
        logger.warning("Analytics unavailable (data tier?): %s", exc)
        payload = {
            "total_sessions": 0,
            "total_hints": 0,
            "satisfaction": {"thumbs_up": 0, "thumbs_down": 0},
            "failure_categories": [],
            "by_module": [],
            "average_latency_ms": 0,
            "unanswered_questions": 0,
            "grounding_categories": [],
            "error": "telemetry unavailable",
        }
    return jsonify(payload), 200


def _build_analytics() -> dict:
    with session_scope() as s:
        total_hints = int(
            s.execute(select(func.count()).select_from(TelemetryLog)).scalar_one()
        )
        total_sessions = int(
            s.execute(
                select(func.count(distinct(TelemetryLog.session_id)))
            ).scalar_one()
        )
        avg_latency = s.execute(
            select(func.avg(TelemetryLog.response_latency_ms))
        ).scalar_one()

        thumbs_up = int(
            s.execute(
                select(func.count())
                .select_from(HintFeedback)
                .where(HintFeedback.rating == 1)
            ).scalar_one()
        )
        thumbs_down = int(
            s.execute(
                select(func.count())
                .select_from(HintFeedback)
                .where(HintFeedback.rating == -1)
            ).scalar_one()
        )

        # Failure breakdown by thumbs-down reason tag, including explicit zeros so
        # the dashboard shows every category rather than only the ones hit.
        reason_rows = dict(
            s.execute(
                select(HintFeedback.reason_tag, func.count())
                .where(HintFeedback.rating == -1, HintFeedback.reason_tag.isnot(None))
                .group_by(HintFeedback.reason_tag)
            ).all()
        )
        failure_categories = [
            {
                "reason_tag": tag,
                "label": _REASON_LABELS.get(tag, tag),
                "count": int(reason_rows.get(tag, 0)),
            }
            for tag in FEEDBACK_REASON_TAGS
        ]

        module_names = {
            mid: name
            for mid, name in s.execute(select(Module.module_id, Module.module_name)).all()
        }

        # Per-module satisfaction.
        per_module = []
        for module_id, name in sorted(module_names.items()):
            up = int(
                s.execute(
                    select(func.count())
                    .select_from(HintFeedback)
                    .where(HintFeedback.module_id == module_id, HintFeedback.rating == 1)
                ).scalar_one()
            )
            down = int(
                s.execute(
                    select(func.count())
                    .select_from(HintFeedback)
                    .where(HintFeedback.module_id == module_id, HintFeedback.rating == -1)
                ).scalar_one()
            )
            responses = up + down
            per_module.append(
                {
                    "module_id": module_id,
                    "module_name": name,
                    "satisfaction_rate": (up / responses) if responses else 0.0,
                    "responses": responses,
                }
            )

        open_questions = int(
            s.execute(
                select(func.count())
                .select_from(UnansweredQuestion)
                .where(UnansweredQuestion.status == "open")
            ).scalar_one()
        )
        # ``retrieval_categories`` is a JSONB array, so it is counted in Python
        # rather than grouped in SQL: grouping on a JSONB column is both fragile
        # across pgvector/pg versions and slower than the table is large enough
        # here to matter.
        category_rows = s.execute(
            select(TelemetryLog.retrieval_categories).where(
                TelemetryLog.retrieval_categories.isnot(None)
            )
        ).scalars().all()
        category_counts: dict[str, int] = {}
        for categories in category_rows:
            for category in categories or ["none"]:
                category_counts[category] = category_counts.get(category, 0) + 1

    return {
        "total_sessions": total_sessions,
        "total_hints": total_hints,
        "satisfaction": {"thumbs_up": thumbs_up, "thumbs_down": thumbs_down},
        "failure_categories": failure_categories,
        "by_module": per_module,
        "average_latency_ms": int(avg_latency or 0),
        "unanswered_questions": open_questions,
        "grounding_categories": [
            {"category": key, "count": count}
            for key, count in sorted(category_counts.items(), key=lambda kv: -kv[1])
        ],
    }
