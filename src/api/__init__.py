"""HTTP blueprints for the API Gateway (Tier 2).

* :mod:`src.api.chat_routes`   - ``/api/chat`` and ``/api/feedback``
* :mod:`src.api.admin_routes`  - ``/api/admin/*`` model configuration
* :mod:`src.api.tutor_routes`  - ``/api/tutor/*`` ungrounded-question queue
"""

from .admin_routes import admin_bp
from .chat_routes import chat_bp
from .tutor_routes import tutor_bp

__all__ = ["admin_bp", "chat_bp", "tutor_bp"]
