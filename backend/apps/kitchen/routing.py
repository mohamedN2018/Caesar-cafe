from django.urls import re_path

from .consumers import BranchConsumer

websocket_urlpatterns = [
    re_path(
        r"^ws/branch/(?P<branch_id>[0-9a-f-]{36})/$",
        BranchConsumer.as_asgi(),
        name="branch-socket",
    ),
]
