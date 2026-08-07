"""
The authenticated principal, resolved once per request.

Three distinct principals exist (docs/03). Conflating them is a common and
serious mistake, so they are separate types here:

  WEB     — a human on the Web Admin. Management API.
  DEVICE  — an activated terminal with no human attached. Sync + reads only;
            it can keep the outbox draining at 3am but cannot take money.
  POS     — a device WITH a human logged in. Adds that person's permissions,
            and their id is what lands in the audit log.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from uuid import UUID


class PrincipalKind(StrEnum):
    ANONYMOUS = "ANONYMOUS"
    WEB = "WEB"
    DEVICE = "DEVICE"
    POS = "POS"
    ENROLLMENT = "ENROLLMENT"
    """
    Password verified, but the account still owes a security step (MFA
    enrolment). Carries NO permissions and may reach only the enrolment
    endpoints. Without this, policy-mandated MFA is a deadlock: login refuses a
    token until you enrol, and enrolling requires a token.
    """


@dataclass(frozen=True)
class AuthContext:
    kind: PrincipalKind = PrincipalKind.ANONYMOUS
    user_id: UUID | None = None
    organization_id: UUID | None = None
    branch_id: UUID | None = None
    device_id: UUID | None = None
    permissions: frozenset[str] = field(default_factory=frozenset)
    is_superuser: bool = False

    @property
    def is_authenticated(self) -> bool:
        return self.kind is not PrincipalKind.ANONYMOUS

    @property
    def is_fully_authenticated(self) -> bool:
        """Authenticated AND owing nothing further. Enrolment tokens are not."""
        return self.is_authenticated and self.kind is not PrincipalKind.ENROLLMENT

    @property
    def has_human(self) -> bool:
        """True when a person is accountable for this request."""
        return self.kind in (PrincipalKind.WEB, PrincipalKind.POS)

    def has(self, code: str) -> bool:
        return self.is_superuser or code in self.permissions

    def has_any(self, *codes: str) -> bool:
        return any(self.has(code) for code in codes)

    def has_all(self, *codes: str) -> bool:
        return all(self.has(code) for code in codes)


ANONYMOUS = AuthContext()
