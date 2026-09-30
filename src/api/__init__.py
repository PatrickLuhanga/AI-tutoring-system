"""HTTP blueprints for the API Gateway (Tier 2).

* :mod:`src.api.auth_routes`     - ``/api/auth/*`` signup, login, sessions
* :mod:`src.api.chat_routes`     - ``/api/chat`` and ``/api/feedback``
* :mod:`src.api.admin_routes`    - ``/api/admin/*`` model config + analytics
* :mod:`src.api.tutor_routes`    - ``/api/tutor/*`` ungrounded-question queue
* :mod:`src.api.practice_routes` - ``/api/practice/*`` question bank + tests
* :mod:`src.api.notification_routes` - ``/api/notifications`` announcements
* :mod:`src.api.admin_accounts_routes` - ``/api/accounts`` + ``/api/content``
* :mod:`src.api.resource_routes` - ``/resources/*`` the linkable corpus library
"""

from .admin_accounts_routes import admin_accounts_bp, content_bp
from .admin_routes import admin_bp
from .auth_routes import auth_bp
from .chat_routes import chat_bp
from .notification_routes import notification_bp
from .practice_routes import practice_bp
from .resource_routes import resource_bp
from .tutor_routes import tutor_bp

__all__ = [
    "admin_accounts_bp",
    "admin_bp",
    "auth_bp",
    "chat_bp",
    "content_bp",
    "notification_bp",
    "practice_bp",
    "resource_bp",
    "tutor_bp",
]
