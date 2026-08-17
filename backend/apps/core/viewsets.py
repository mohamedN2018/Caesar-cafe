"""
Base ViewSet for tenant-scoped resources.

Every Phase 4+ resource inherits this, so branch scoping and permission
declaration are the default rather than something each new endpoint has to
remember. `tests/test_permission_coverage.py` proves nothing slipped through.
"""

from __future__ import annotations

from rest_framework import viewsets

from apps.authz.drf import HasPermission, IsAuthenticatedPrincipal, auth_context
from apps.core.exceptions import AppError


class BranchScopedViewSet(viewsets.ModelViewSet):
    """
    Reads and writes are confined to the caller's branch.

    `branch` and `organization` are injected from the authenticated principal
    and NEVER read from the request body — otherwise any authenticated user
    could write into another tenant by editing a payload (threat I1).
    """

    permission_classes = [IsAuthenticatedPrincipal, HasPermission]

    def get_queryset(self):
        principal = auth_context(self.request)
        queryset = super().get_queryset()

        if principal.branch_id is not None:
            return queryset.filter(branch_id=principal.branch_id)
        # No branch selected yet (a fresh web login): stay inside the org.
        return queryset.filter(organization_id=principal.organization_id)

    def perform_create(self, serializer) -> None:
        principal = auth_context(self.request)
        if principal.branch_id is None:
            raise AppError("يجب اختيار الفرع أولاً", code="BRANCH_REQUIRED", status_code=400)
        serializer.save(
            organization_id=principal.organization_id,
            branch_id=principal.branch_id,
            created_by_id=principal.user_id,
        )

    def perform_update(self, serializer) -> None:
        serializer.save(updated_by_id=auth_context(self.request).user_id)

    def perform_destroy(self, instance) -> None:
        """
        Deactivate rather than delete.

        A product that has ever been sold must not be removable: deleting it
        would orphan historical line items and silently rewrite last quarter's
        reports (docs/02).

        **`deactivated_at` is stamped only where it exists.** This branched on
        `is_active` and then set both fields, which is a different test from the
        one it needs: three models carry `is_active` without inheriting
        `SoftDeletableModel` — `PaymentMethod`, `Recipe` and `User` — and for the
        two of those with a DELETE route, every attempt returned 500.

        `DELETE /payments/methods/{id}/` had therefore never worked, from the day
        it was routed. Nothing caught it because nothing called it: there was no
        screen for payment methods at all, which is how a broken endpoint stays
        broken quietly.
        """
        if not hasattr(instance, "is_active"):
            instance.delete()
            return

        from django.utils import timezone

        instance.is_active = False
        fields = ["is_active", "updated_at"]
        if hasattr(instance, "deactivated_at"):
            instance.deactivated_at = timezone.now()
            fields.insert(1, "deactivated_at")
        instance.save(update_fields=fields)
