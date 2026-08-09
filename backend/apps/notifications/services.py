"""
Getting an alert onto a phone.

The interesting decisions here are all about restraint and cleanup:

  * **An alert is raised once per subject.** `SentAlert` has a unique constraint
    on (branch, kind, dedupe_key), and the insert is what claims it — not a
    prior SELECT, which two workers could both pass.
  * **A dead subscription is deleted, not retried.** A 404 or 410 from a push
    service means the browser is gone. Keeping the row would mean failing
    forever against an endpoint that will never answer.
  * **Delivery failure never fails the caller.** Nothing in the cafe depends on
    a notification arriving, and a push service having a bad afternoon must not
    take down the task that also checks the backups.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx
from django.conf import settings
from django.db import IntegrityError, transaction

from .alerts import Alert
from .models import PushSubscription, SentAlert
from .webpush import PushError, VapidKeys, build_request

logger = logging.getLogger(__name__)

#: After this many consecutive failures a subscription is dropped even without
#: an explicit 410. A push service that has refused six times is not going to
#: accept the seventh, and the row is costing an HTTP request every few minutes.
MAX_FAILURES = 6

#: Short. The task runs on a schedule and a push service that is slow right now
#: will be tried again in minutes — blocking a worker on it helps nobody.
TIMEOUT = httpx.Timeout(connect=4.0, read=8.0, write=8.0, pool=4.0)


class NotConfigured(RuntimeError):
    """
    No VAPID key pair.

    Raised rather than silently skipped: a deployment that believes it is
    sending alerts and is not is worse than one that says it cannot.
    """


@dataclass(frozen=True)
class DeliveryResult:
    sent: int
    dropped: int
    failed: int


def keys() -> VapidKeys:
    private = getattr(settings, "VAPID_PRIVATE_KEY", "")
    public = getattr(settings, "VAPID_PUBLIC_KEY", "")
    if not private or not public:
        raise NotConfigured(
            "VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY are unset. "
            "Generate a pair with `python manage.py generate_vapid_keys`."
        )
    return VapidKeys(private_key=private, public_key=public)


def subject() -> str:
    """
    The `sub` claim: how a push service reaches the operator.

    Used exactly once — the day deliveries start failing — which is why it is a
    real address from settings rather than a placeholder nobody updated.
    """
    return getattr(settings, "VAPID_SUBJECT", "") or "mailto:ops@caesar.local"


def claim(branch, alert: Alert) -> SentAlert | None:
    """
    Record the alert, or return None if this subject has already been raised.

    The INSERT is the claim. Checking first and inserting second would let two
    workers evaluating the same branch both pass the check and both notify.
    """
    try:
        with transaction.atomic():
            return SentAlert.objects.create(
                branch=branch,
                kind=alert.kind,
                dedupe_key=alert.dedupe_key,
                title=alert.title,
                body=alert.body,
                url=alert.url,
            )
    except IntegrityError:
        return None


def recipients(branch):
    """
    Who hears about this branch.

    Scoped to subscriptions in the branch, so an owner with two cafes is not
    woken by the other one's kitchen.
    """
    return PushSubscription.objects.filter(branch=branch).select_related("user")


def deliver(record: SentAlert, *, client: httpx.Client | None = None) -> DeliveryResult:
    """
    Push one recorded alert to every subscription on its branch.

    Returns counts rather than raising: the caller is a scheduled task whose job
    is to keep going.
    """
    pair = keys()
    payload = {
        "title": record.title,
        "body": record.body,
        "url": record.url,
        "kind": record.kind,
        "tag": f"{record.kind}:{record.dedupe_key}",
    }

    sent = dropped = failed = 0
    owned = client is None
    client = client or httpx.Client(timeout=TIMEOUT)

    try:
        for subscription in recipients(record.branch):
            outcome = _send_one(client, subscription, payload, pair)
            if outcome == "sent":
                sent += 1
            elif outcome == "dropped":
                dropped += 1
            else:
                failed += 1
    finally:
        if owned:
            client.close()

    if sent:
        SentAlert.objects.filter(pk=record.pk).update(delivered=sent)

    return DeliveryResult(sent=sent, dropped=dropped, failed=failed)


def _send_one(client: httpx.Client, subscription: PushSubscription, payload: dict, pair) -> str:
    from django.utils import timezone

    try:
        url, body, headers = build_request(
            endpoint=subscription.endpoint,
            client_public_key=subscription.p256dh,
            auth_secret=subscription.auth,
            payload=payload,
            keys=pair,
            subject=subject(),
        )
        response = client.post(url, content=body, headers=headers)
    except (httpx.HTTPError, PushError, ValueError):
        # A malformed subscription counts as a failure, not a crash: one bad row
        # must not stop the other phones being told.
        logger.warning("Push delivery failed", extra={"subscription": str(subscription.pk)})
        _record_failure(subscription)
        return "failed"

    # 404/410 is the push service saying this endpoint is gone for good. The
    # only correct response is to forget it.
    if response.status_code in (404, 410):
        logger.info("Push subscription gone", extra={"subscription": str(subscription.pk)})
        subscription.delete()
        return "dropped"

    if response.status_code >= 400:
        logger.warning(
            "Push service refused",
            extra={"subscription": str(subscription.pk), "code": response.status_code},
        )
        _record_failure(subscription)
        return "failed"

    PushSubscription.objects.filter(pk=subscription.pk).update(
        last_sent_at=timezone.now(), failures=0
    )
    return "sent"


def _record_failure(subscription: PushSubscription) -> None:
    failures = subscription.failures + 1
    if failures >= MAX_FAILURES:
        logger.info(
            "Dropping a subscription after repeated failures",
            extra={"subscription": str(subscription.pk), "failures": failures},
        )
        subscription.delete()
        return
    PushSubscription.objects.filter(pk=subscription.pk).update(failures=failures)


def run_for_branch(branch, *, client: httpx.Client | None = None) -> DeliveryResult:
    """Evaluate, deduplicate, and deliver — the whole cycle for one branch."""
    from .alerts import evaluate

    totals = DeliveryResult(0, 0, 0)
    for alert in evaluate(branch):
        record = claim(branch, alert)
        if record is None:
            continue
        result = deliver(record, client=client)
        totals = DeliveryResult(
            totals.sent + result.sent,
            totals.dropped + result.dropped,
            totals.failed + result.failed,
        )
    return totals
