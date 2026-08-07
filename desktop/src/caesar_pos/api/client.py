"""
HTTP client for the Caesar API.

Every response is enveloped, so unwrapping and error mapping happen once here.
Callers get `data` and a typed exception carrying the server's stable code.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..config import APP_VERSION

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=30.0, write=30.0, pool=5.0)


class ApiError(Exception):
    """Any non-2xx response, carrying the server's stable machine code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        status: int | None = None,
        errors: dict[str, Any] | None = None,
        detail: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.errors = errors or {}
        self.detail = detail or {}
        self.request_id = request_id

    def __str__(self) -> str:
        return self.message


class NetworkUnavailable(ApiError):
    """The request never reached the application. Distinct from a rejection."""

    def __init__(self, message: str = "تعذر الاتصال بالخادم. تحقق من الإنترنت.") -> None:
        super().__init__("NETWORK_UNAVAILABLE", message)


@dataclass
class ApiClient:
    """
    Thin wrapper over httpx.

    `access_token` is mutable so the sync worker can refresh it without the
    caller rebuilding the client.
    """

    base_url: str
    access_token: str | None = None
    timeout: httpx.Timeout = field(default_factory=lambda: DEFAULT_TIMEOUT)
    _client: httpx.Client | None = None

    def __post_init__(self) -> None:
        self.base_url = self.base_url.rstrip("/")
        if not self.base_url.endswith("/api/v1"):
            self.base_url = f"{self.base_url}/api/v1"

    @property
    def client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                timeout=self.timeout,
                headers={
                    "Content-Type": "application/json",
                    "User-Agent": f"CaesarPOS/{APP_VERSION}",
                },
                # TLS verification is never disabled — not even behind a debug
                # flag, because debug flags ship (docs/09).
                verify=True,
                follow_redirects=False,
            )
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    # ── requests ─────────────────────────────────────────────────────────────

    def request(
        self,
        method: str,
        path: str,
        *,
        json: dict | None = None,
        params: dict | None = None,
        authenticated: bool = True,
        idempotency_key: str | None = None,
    ) -> Any:
        headers: dict[str, str] = {}
        if authenticated and self.access_token:
            headers["Authorization"] = f"Bearer {self.access_token}"
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key

        try:
            response = self.client.request(method, path, json=json, params=params, headers=headers)
        except httpx.TimeoutException as exc:
            raise NetworkUnavailable("انتهت مهلة الاتصال بالخادم.") from exc
        except httpx.TransportError as exc:
            raise NetworkUnavailable() from exc

        return self._unwrap(response)

    def get(self, path: str, **kwargs) -> Any:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, json: dict | None = None, **kwargs) -> Any:
        return self.request("POST", path, json=json, **kwargs)

    def patch(self, path: str, json: dict | None = None, **kwargs) -> Any:
        return self.request("PATCH", path, json=json, **kwargs)

    # ── envelope ─────────────────────────────────────────────────────────────

    @staticmethod
    def _unwrap(response: httpx.Response) -> Any:
        try:
            body = response.json()
        except ValueError:
            # A non-JSON body means a proxy or gateway answered, not the app.
            raise ApiError(
                "BAD_GATEWAY",
                "استجابة غير متوقعة من الخادم.",
                status=response.status_code,
            ) from None

        request_id = (body.get("meta") or {}).get("request_id")

        if response.is_success and body.get("success"):
            return body.get("data")

        raise ApiError(
            code=body.get("code", "REQUEST_FAILED"),
            message=body.get("message", "حدث خطأ أثناء تنفيذ العملية"),
            status=response.status_code,
            errors=body.get("errors"),
            detail=body.get("detail"),
            request_id=request_id,
        )
