"""
ASGI entrypoint.

Serves HTTP and, from Phase 6, WebSockets for the kitchen display. Run as a
separate process from Gunicorn so a slow report query can never starve the
kitchen's socket pool.
"""

import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.prod")

django_asgi_app = get_asgi_application()

# Phase 6 replaces this with a ProtocolTypeRouter carrying the websocket routes.
application = django_asgi_app
