"""
Make the OpenAPI schema describe what clients actually receive.

Every response goes through EnvelopeJSONRenderer, so the raw serializer shape is
never the wire format. Without this, generated TypeScript types would describe
`{status, version}` while the client actually gets
`{success, data: {status, version}, meta}` — and the "schema is the contract"
guarantee in §32 would be false from day one.
"""

from __future__ import annotations

from typing import Any

META_SCHEMA = {
    "type": "object",
    "properties": {
        "request_id": {
            "type": "string",
            "description": "Correlates this response with server logs.",
        }
    },
}

ERROR_ENVELOPE = {
    "type": "object",
    "properties": {
        "success": {"type": "boolean", "enum": [False]},
        "message": {"type": "string", "description": "Localized, safe to display."},
        "code": {
            "type": "string",
            "description": "Stable machine-readable code. Never localized.",
            "example": "VALIDATION_ERROR",
        },
        "errors": {
            "type": "object",
            "additionalProperties": {"type": "array", "items": {"type": "string"}},
            "description": "Per-field messages, for form binding.",
        },
        "meta": META_SCHEMA,
    },
    "required": ["success", "message", "code"],
}


def wrap_in_envelope(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "success": {"type": "boolean", "enum": [True]},
            "data": schema,
            "meta": META_SCHEMA,
        },
        "required": ["success", "data"],
    }


def _is_enveloped(schema: dict[str, Any]) -> bool:
    return "success" in (schema.get("properties") or {})


def envelope_postprocessing_hook(result, generator, request, public):
    """
    drf-spectacular postprocessing hook.

    Wraps every 2xx response body in the success envelope, and points every
    non-2xx body at the shared error envelope — because every failure goes
    through `envelope_exception_handler`, whatever the view declared.
    """
    error_ref = {"$ref": "#/components/schemas/ErrorEnvelope"}

    for path_item in (result.get("paths") or {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            for status_code, response in (operation.get("responses") or {}).items():
                content = response.get("content") or {}
                is_success = str(status_code).startswith("2")

                for media in content.values():
                    schema = media.get("schema")
                    if not isinstance(schema, dict):
                        continue
                    if is_success:
                        if not _is_enveloped(schema):
                            media["schema"] = wrap_in_envelope(schema)
                    else:
                        media["schema"] = error_ref

                if not is_success and not content:
                    response["content"] = {"application/json": {"schema": error_ref}}

    result.setdefault("components", {}).setdefault("schemas", {})["ErrorEnvelope"] = ERROR_ENVELOPE
    return result
