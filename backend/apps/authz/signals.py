"""
Cache invalidation on any authorization change.

A revoked permission that lingers for five minutes is a security hole, so this
is deliberately eager and coarse — correctness over cache hit rate.
"""

from django.db.models.signals import post_delete, post_save
from django.dispatch import receiver

from .models import Role, RoleAssignment, RoleLimit, RolePermission
from .services import invalidate_all, invalidate_user


@receiver([post_save, post_delete], sender=RoleAssignment)
def _on_assignment_change(instance, **kwargs):
    invalidate_user(instance.user_id)


@receiver([post_save, post_delete], sender=RolePermission)
@receiver([post_save, post_delete], sender=RoleLimit)
@receiver([post_save, post_delete], sender=Role)
def _on_role_change(**kwargs):
    # A role's permissions changed: every holder is affected, and we do not know
    # who they are without a query. Clearing everything is the safe move.
    invalidate_all()
