"""HTTP blueprints for the API Gateway (Tier 2).

* :mod:`src.api.chat_routes`     - ``/api/chat`` and ``/api/feedback``
* :mod:`src.api.admin_routes`    - ``/api/admin/*`` model config + analytics
* :mod:`src.api.tutor_routes`    - ``/api/tutor/*`` ungrounded-question queue
* :mod:`src.api.resource_routes` - ``/resources/*`` the linkable corpus library
"""

from .admin_routes import admin_bp
from .chat_routes import chat_bp
from .resource_routes import resource_bp
from .tutor_routes import tutor_bp

__all__ = ["admin_bp", "chat_bp", "resource_bp", "tutor_bp"]
