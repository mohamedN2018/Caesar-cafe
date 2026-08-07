"""
Response envelope.

Every response — success, failure, paginated — has the same shape so the SPA
and the Desktop share one parser and one error path (docs/03-api-map.md).

    {"success": true, "data": {...}, "meta": {"request_id": "..."}}
"""

from __future__ import annotations

from typing import Any

from rest_framework.renderers import JSONRenderer


class EnvelopeJSONRenderer(JSONRenderer):
    def render(
        self,
        data: Any,
        accepted_media_type: str | None = None,
        renderer_context: dict | None = None,
    ) -> bytes:
        renderer_context = renderer_context or {}
        response = renderer_context.get("response")
        request = renderer_context.get("request")

        request_id = getattr(request, "request_id", None)

        # Already enveloped (errors from the exception handler, or a view that
        # built its own paginated envelope) — just stamp the request id.
        if isinstance(data, dict) and "success" in data:
            meta = data.setdefault("meta", {})
            if isinstance(meta, dict) and request_id:
                meta.setdefault("request_id", request_id)
            return super().render(data, accepted_media_type, renderer_context)

        status_code = getattr(response, "status_code", 200)
        if status_code >= 400:
            payload = {
                "success": False,
                "message": _extract_message(data),
                "code": "REQUEST_FAILED",
                "errors": data if isinstance(data, dict) else {},
                "meta": {"request_id": request_id},
            }
        else:
            payload = {
                "success": True,
                "data": data,
                "meta": {"request_id": request_id},
            }

        return super().render(payload, accepted_media_type, renderer_context)


def _extract_message(data: Any) -> str:
    if isinstance(data, dict):
        detail = data.get("detail")
        if isinstance(detail, str):
            return detail
    if isinstance(data, str):
        return data
    return "حدث خطأ أثناء تنفيذ العملية"
