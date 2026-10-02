"""Flask API Gateway - the front door of the Orchestration Tier (section 3).

Start the server with::

    python -m src.app

The application is built with an application factory (:func:`create_app`) so the
same code is importable by a WSGI server (gunicorn/waitress) and testable in
isolation. All heavy collaborators (the LLM router, retriever and agent
workflow) are constructed once per app and stored in ``app.extensions``.

Routes
------
``POST /api/chat``                  student question -> audited Socratic hint
``POST /api/feedback``              thumbs up/down -> telemetry
``GET  /api/modules``               modules available to the client dropdown
``GET  /api/health``                liveness / readiness probe
``GET/POST /api/admin/llm-config``  switch Cloud <-> Local LLM, rotate API key
``GET  /api/admin/ollama-models``   list local Ollama models for the dropdown
``GET  /api/tutor/questions``       queue of questions RAG could not ground
``POST /api/tutor/questions/<id>/answer``  answer one, optionally promoting it
"""

from __future__ import annotations

import logging
import sys

from flask import Flask, jsonify, request

from .agents import TutoringWorkflow
from .api import (
    admin_accounts_bp,
    admin_bp,
    auth_bp,
    chat_bp,
    content_bp,
    history_bp,
    notification_bp,
    practice_bp,
    resource_bp,
    tutor_bp,
)
from .auth import AuthError
from .config import settings
from .db import check_connection
from .inference import INFERENCE_UNAVAILABLE_MESSAGE
from .llm_router import LLMError, LLMRouter
from .retriever import Retriever
from .secrets_store import SecretStoreError

logger = logging.getLogger(__name__)


def _configure_logging() -> None:
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.DEBUG if settings.flask_debug else logging.INFO,
            format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        )


def _configure_cors(app: Flask) -> None:
    """Enable browser access for the Client Tier when flask-cors is available.

    A wildcard origin with credentialed requests is refused outright rather
    than quietly honoured: browsers reject that combination anyway, and a
    configuration that looks permissive but silently drops credentials is worse
    than one that fails loudly and says what to set instead.

    The wildcard is still the default, because in local development the Vite dev
    server runs on a different port and nothing else is listening. It is
    environment-controlled so a deployment can pin it without a code change.
    """
    try:
        from flask_cors import CORS
    except ImportError:  # pragma: no cover - optional dependency
        logger.debug("flask-cors not installed; skipping CORS setup.")
        return

    origins = [
        origin.strip()
        for origin in settings.cors_allowed_origins.split(",")
        if origin.strip()
    ] or ["*"]

    wildcard = "*" in origins
    if wildcard:
        logger.warning(
            "CORS_ALLOWED_ORIGINS is '*' - any origin may call the API. Fine for "
            "local development; pin it to the deployed frontend origin before "
            "hosting this anywhere."
        )

    CORS(
        app,
        resources={r"/api/*": {"origins": origins}},
        allow_headers=["Content-Type", "X-User-Email", "X-User-Role", "X-Admin-Key", "X-DUT4life-Token"],
        # The session travels in an Authorization header, not a cookie, so the
        # wildcard origin above is not paired with ambient credentials.
        supports_credentials=False,
    )


def _register_error_handlers(app: Flask) -> None:
    @app.errorhandler(AuthError)
    def _auth_error(exc: AuthError):
        return jsonify({"error": exc.message}), exc.status_code

    @app.errorhandler(LLMError)
    def _llm_error(exc: LLMError):
        # The inference tier is down/time-out: answer with the clean, stable
        # payload the UI expects rather than leaking transport detail.
        logger.error("LLM backend error: %s", exc)
        return jsonify({"error": INFERENCE_UNAVAILABLE_MESSAGE}), 502

    @app.errorhandler(SecretStoreError)
    def _secret_error(exc: SecretStoreError):
        return jsonify({"error": str(exc)}), 500

    @app.errorhandler(404)
    def _not_found(_exc):
        return jsonify({"error": "Not found."}), 404

    @app.errorhandler(405)
    def _method_not_allowed(_exc):
        return jsonify({"error": "Method not allowed."}), 405

    @app.errorhandler(500)
    def _server_error(_exc):  # pragma: no cover - defensive
        return jsonify({"error": "Internal server error."}), 500

    @app.errorhandler(Exception)
    def _unhandled(exc):  # noqa: BLE001 - last line of defence
        # A crash in any downstream tier must surface as clean JSON, never as a
        # dropped connection or an HTML traceback the React client cannot parse.
        from werkzeug.exceptions import HTTPException

        if isinstance(exc, HTTPException):
            return exc
        logger.exception("Unhandled error while serving a request: %s", exc)
        return jsonify({"error": "Internal server error."}), 500


def _register_meta_routes(app: Flask) -> None:
    @app.get("/api/health")
    def health():
        deep = request.args.get("deep") in {"1", "true", "yes"}
        db_ok, db_info = True, ""
        try:
            db_info = check_connection()
        except Exception as exc:  # noqa: BLE001
            db_ok, db_info = False, str(exc)

        router = app.extensions["llm_router"]
        payload = {
            "status": "ok" if db_ok else "degraded",
            "database": {"ok": db_ok, "info": db_info[:120]},
            "ollama": {
                "configured": True,
                "base_url": settings.ollama_base_url,
                "circuit": router.ollama_status(),
            },
        }
        try:
            payload["llm"] = router.describe_active()
        except Exception as exc:  # noqa: BLE001
            payload["llm"] = {"error": str(exc)}

        if deep:
            probe = router.ollama_status(probe=True)
            payload["ollama"].update(probe)
            if probe.get("reachable"):
                try:
                    payload["ollama"]["models"] = router.list_ollama_models()
                except LLMError as exc:
                    payload["ollama"]["models"] = []
                    payload["ollama"]["error"] = str(exc)

        return jsonify(payload), (200 if db_ok else 503)

    @app.get("/api/modules")
    def modules():
        return (
            jsonify(
                {
                    "modules": [
                        {
                            "module_id": module["module_id"],
                            "module_code": module.get("module_code"),
                            "module_name": module["module_name"],
                            "language": module.get("language"),
                        }
                        for module in settings.modules.values()
                    ]
                }
            ),
            200,
        )


def create_app() -> Flask:
    """Build and configure the Flask gateway."""
    _configure_logging()

    app = Flask(__name__)
    app.config.update(
        JSON_SORT_KEYS=False,
        MAX_CONTENT_LENGTH=1 * 1024 * 1024,
    )

    # --- Wire the orchestration collaborators (built once) -----------------
    router = LLMRouter()
    retriever = Retriever()
    workflow = TutoringWorkflow(retriever=retriever, router=router)

    app.extensions["llm_router"] = router
    app.extensions["llm_config_service"] = router.config_service
    app.extensions["retriever"] = retriever
    app.extensions["tutoring_workflow"] = workflow

    # Best-effort bootstrap: make sure the active config row exists.
    try:
        router.config_service.seed_default()
    except Exception as exc:  # noqa: BLE001 - server should still start
        logger.warning(
            "Could not bootstrap the LLM configuration (is PostgreSQL running and "
            "did you run `python -m src.setup_database`?): %s",
            exc,
        )

    app.register_blueprint(auth_bp)
    app.register_blueprint(chat_bp)
    app.register_blueprint(history_bp)
    app.register_blueprint(admin_bp)
    app.register_blueprint(admin_accounts_bp)
    app.register_blueprint(tutor_bp)
    app.register_blueprint(practice_bp)
    app.register_blueprint(notification_bp)
    app.register_blueprint(content_bp)
    app.register_blueprint(resource_bp)
    _register_meta_routes(app)
    _register_error_handlers(app)
    _configure_cors(app)

    logger.info(
        "Gateway ready. Active provider: %s",
        settings.default_llm_provider,
    )
    return app


def main() -> int:
    app = create_app()
    logger.info(
        "Starting API Gateway on http://%s:%s (debug=%s)",
        settings.flask_host,
        settings.flask_port,
        settings.flask_debug,
    )
    app.run(host=settings.flask_host, port=settings.flask_port, debug=settings.flask_debug)
    return 0


if __name__ == "__main__":
    sys.exit(main())
