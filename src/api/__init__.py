"""HTTP blueprints for the API Gateway (Tier 2).

* :mod:`src.api.chat_routes`      - ``/api/chat`` and ``/api/feedback``
* :mod:`src.api.admin_routes`     - ``/api/admin/*`` model configuration
* :mod:`src.api.analytics_routes` - sessions + tutor/admin dashboard analytics
* :mod:`src.api.profile_routes`   - dynamic login, profile and tutor grants
* :mod:`src.api.history_routes`   - chat-history sessions (list/load/delete)
"""

from .admin_routes import admin_bp
from .analytics_routes import analytics_bp
from .chat_routes import chat_bp
from .history_routes import history_bp
from .profile_routes import profile_bp

__all__ = ["admin_bp", "analytics_bp", "chat_bp", "history_bp", "profile_bp"]
