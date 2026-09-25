"""API Gateway entry point for the Hybrid AI Tutoring System (Tier 2).

This file is deliberately tiny. It contains **no routes, no prompts and no HTTP
logic** - it only builds the application factory from :mod:`src.app` and runs it.
Everything else lives behind clear seams:

* route definitions  -> ``src/api/`` (Flask blueprints: chat, admin)
* agent logic        -> ``src/agents/`` (intent, scaffolding, tutor, guardrail, workflow)
* inference boundary -> ``src/inference/`` (timeout + circuit breaker around Ollama)
* data access        -> ``src/db.py``, ``src/retriever.py``

Run with::

    python app.py

or serve the exported WSGI callable::

    waitress-serve --call app:create_app
    gunicorn "src.app:create_app()"
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow `python app.py` from any working directory.
PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.app import create_app, main  # noqa: E402

#: WSGI callable (gunicorn/waitress). Built once at import time; constructing the
#: gateway touches no heavy resource and keeps serving even if Postgres is down.
app = create_app()


if __name__ == "__main__":
    raise SystemExit(main())
