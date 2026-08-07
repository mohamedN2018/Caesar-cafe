"""
Request-scoped plumbing.

RequestIDMiddleware gives every request a stable id that appears in the response
body, the `X-Request-ID` header, and every log line for that request. When a
cashier reports a problem, that one string retrieves the whole server-side story.
"""

from __future__ import annotations

import contextvars
import time
import uuid
from collections.abc import Callable

from django.http import HttpRequest, HttpResponse

current_request_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "current_request_id", default=None
)

HEADER = "HTTP_X_REQUEST_ID"


class RequestIDMiddleware:
    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        # Accept an upstream id (the reverse proxy may set one) so a single
        # trace spans the proxy and the app; otherwise mint one.
        request_id = request.META.get(HEADER) or uuid.uuid4().hex
        request.request_id = request_id  # type: ignore[attr-defined]
        token = current_request_id.set(request_id)

        started = time.perf_counter()
        try:
            response = self.get_response(request)
        finally:
            current_request_id.reset(token)

        response["X-Request-ID"] = request_id
        response["X-Response-Time-ms"] = f"{(time.perf_counter() - started) * 1000:.1f}"
        return response
