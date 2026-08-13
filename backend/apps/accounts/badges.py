"""
The QR badge a cashier scans instead of typing a PIN.

A badge is a **second way to say the same thing as a PIN**, not a stronger one.
Both are weak secrets that are only ever accepted from an activated device, and
the sentence that governs both is in `models.py`: the device proves the request
comes from a terminal the branch owns, and the PIN or badge only decides *which
human is standing at it*.

Being honest about what this is worth:

  * **A badge is a password on a piece of paper.** Anybody who photographs it
    can present it. That is an acceptable risk here and nowhere else, because
    the device binding means the photograph is useless from outside the cafe —
    an attacker needs the badge *and* physical access to an enrolled terminal,
    at which point they could have watched somebody type a PIN anyway.
  * **So it is revocable in one click and it is logged like a PIN.** Reprinting
    a badge invalidates the old one, because a badge that was left on a counter
    is exactly the case this has to answer.
  * It carries the person's NAME, so the printed card is self-describing. A
    drawer of identical QR codes is a drawer nobody can sort.

The stored value is a hash. The plaintext is shown once, at issue, in the
response that prints the card — the same rule the licence keys follow.
"""

from __future__ import annotations

import hashlib
import secrets

from django.db import models
from django.utils import timezone

from apps.core.models import BaseModel

#: Long enough that guessing is not a strategy, short enough to fit a QR at a
#: size a phone camera reads from 20cm in cafe lighting.
BADGE_BYTES = 24

#: What every badge string starts with, so a scanner that reads some other QR
#: in the room — a product barcode, a WiFi card — is rejected as the wrong kind
#: of thing rather than counted as a failed sign-in attempt against nobody.
BADGE_PREFIX = "QSRB1."


def mint() -> str:
    """A fresh badge secret. Returned once and never stored in the clear."""
    return BADGE_PREFIX + secrets.token_urlsafe(BADGE_BYTES)


def fingerprint(raw: str) -> str:
    """
    SHA-256, deliberately NOT a slow password hash.

    A badge has full 192-bit entropy, so there is no dictionary to slow down —
    the reason argon2 exists is human-chosen secrets, and this is not one. What
    matters instead is that a lookup is a single indexed query: a cashier holds
    the QR under the scanner and the till has to answer immediately, and
    argon2-per-candidate over every badge in the branch would not.
    """
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def looks_like_a_badge(raw: str) -> bool:
    return raw.startswith(BADGE_PREFIX)


class Badge(BaseModel):
    """One printable card. A person may hold several — a lost one is replaced."""

    user = models.ForeignKey("accounts.User", on_delete=models.CASCADE, related_name="badges")
    #: Indexed and unique: sign-in is a lookup by this, not a scan of every row.
    token_hash = models.CharField(max_length=64, unique=True, db_index=True)

    label = models.CharField(
        max_length=100,
        blank=True,
        help_text="What is written on the card, so a stack of them can be sorted.",
    )
    issued_by = models.ForeignKey(
        "accounts.User", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    last_used_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "staff_badges"
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.label or 'بطاقة'} → {self.user.full_name_ar}"

    @property
    def is_live(self) -> bool:
        return self.revoked_at is None

    def revoke(self) -> None:
        """
        Marked, not deleted. "Which badge was used on the 14th" has to stay
        answerable after the card is destroyed, or the sign-in log points at a
        row that no longer exists.
        """
        if self.revoked_at is None:
            self.revoked_at = timezone.now()
            self.save(update_fields=["revoked_at", "updated_at"])
