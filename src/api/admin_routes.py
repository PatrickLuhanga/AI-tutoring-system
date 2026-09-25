"""Admin-only routes for managing the Dynamic LLM Router (section 10.2).

All routes require the shared admin key (``X-Admin-Key``). API keys are stored
encrypted and never echoed back - ``GET`` returns only whether a key is set and
its last four characters.
"""

from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, request

from ..config import settings
from ..llm_router import LLMConfigService, LLMRouter
from ..auth import require_admin

logger = logging.getLogger(__name__)

admin_bp = Blueprint("admin", __name__, url_prefix="/api/admin")


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
