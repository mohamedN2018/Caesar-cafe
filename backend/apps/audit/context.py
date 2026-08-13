"""
Request context for audit records, carried by a contextvar.

The alternative is threading `request` (or actor + ip + request_id + device_id)
through every service that might audit something. That is four extra parameters
on twenty functions, and the first one somebody forgets produces an audit row
with no IP and no actor — which is exactly the row you need when there is a
dispute.

A contextvar is action at a distance, which is a real cost. It is paid for by
being ONE variable, set in ONE middleware, read in ONE function, and always
optional: an audit call from a Celery task or a management command records what
it knows and leaves the rest null rather than failing.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class AuditContext:
    actor_id: str | None = None
    actor_name: str = ""
    organization_id: str | None = None
    branch_id: str | None = None
    device_id: str | None = None
    ip_address: str | None = None
    user_agent: str = ""
    request_id: str = ""


EMPTY = AuditContext()

_current: contextvars.ContextVar[AuditContext] = contextvars.ContextVar(
    "audit_context", default=EMPTY
)


def current() -> AuditContext:
    return _current.get()


def set_context(context: AuditContext):
    """Returns the token the caller must reset — the middleware does."""
    return _current.set(context)


def reset(token) -> None:
    _current.reset(token)


def override(**fields):
    """
    Narrow the context for one block — used by Celery tasks that act on behalf of
    a known branch, so their audit rows are not orphaned at the org level.
    """
    return _current.set(replace(_current.get(), **fields))
