"""
ASGI entrypoint.

Serves HTTP and the WebSockets that drive the kitchen display. Run as a separate
process from Gunicorn so a slow report query can never starve the kitchen's
socket pool.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

# Must be built before importing anything that touches the app registry.
django_asgi_app = get_asgi_application()

from channels.routing import ProtocolTypeRouter, URLRouter  # noqa: E402
from channels.security.websocket import AllowedHostsOriginValidator  # noqa: E402

from apps.kitchen.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        # OriginValidator rejects cross-site socket attempts: a WebSocket is not
        # covered by CORS, so without this any page could open one against us.
        # Authentication happens in the consumer's connect().
        "websocket": AllowedHostsOriginValidator(URLRouter(websocket_urlpatterns)),
    }
)
