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

    def require_branch(self) -> UUID:
        """
        The caller's branch, or a 400 saying to pick one.

        `branch_id` is optional because a fresh Web login genuinely has no branch
        yet — the user has authenticated and not chosen where they are. Almost
        every caller, though, is about to scope a query by it, and passing None
        into `.filter(branch_id=...)` compiles to `IS NULL`: no rows, no
        explanation, and a screen that looks empty rather than broken.

        Written out longhand at each call site, that check was `filter(...)` →
        `if None` → `raise`, four lines that had to be remembered every time.
        Here it is one call that cannot be forgotten, and the return type says
        so — which is what turns the whole class of mistake into a type error.
        """
        from apps.core.exceptions import AppError

        if self.branch_id is None:
            raise AppError("يجب اختيار الفرع أولاً", code="BRANCH_REQUIRED", status_code=400)
        return self.branch_id

    def require_organization(self) -> UUID:
        """As `require_branch`, for the org-wide screens."""
        from apps.core.exceptions import AppError

        if self.organization_id is None:
            raise AppError("لا توجد مؤسسة على هذا الحساب", code="ORG_REQUIRED", status_code=400)
        return self.organization_id

    def require_user(self) -> UUID:
        """
        The person behind this request.

        Endpoints that change a person's own account are already behind
        `RequiresHuman`, so at runtime this never fires — a bare device token
        cannot reach them. It exists because "already guarded upstream" is a
        fact the type system cannot see, and `User.objects.get(id=None)` raises
        `DoesNotExist`, which reaches the caller as a 404 about a user that does
        exist. If the upstream guard is ever removed, this says what happened.
        """
        from apps.core.exceptions import AppError

        if self.user_id is None:
            raise AppError("هذه العملية تتطلب مستخدماً", code="NOT_AUTHENTICATED", status_code=401)
        return self.user_id

    def has(self, code: str) -> bool:
        return self.is_superuser or code in self.permissions

    def has_any(self, *codes: str) -> bool:
        return any(self.has(code) for code in codes)

    def has_all(self, *codes: str) -> bool:
        return all(self.has(code) for code in codes)


ANONYMOUS = AuthContext()
