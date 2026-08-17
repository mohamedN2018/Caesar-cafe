"""
Invalidation deletes what it names — and nothing else.

The bug this pins: `cache.delete_pattern` is a django-redis extension, this
project runs Django's BUILT-IN RedisCache, and the AttributeError fallback was
`cache.clear()` behind a comment claiming it was for tests. Every settings write
and role change in production was a FLUSHDB. It surfaced as a lock vanishing out
from under a running demo rebuild — the lock was correct; the ground under it
was not.
"""

from __future__ import annotations

import pytest
from django.core.cache import cache

from apps.core.cacheutils import delete_pattern

pytestmark = pytest.mark.django_db


class TestDeletePattern:
    def test_deletes_only_the_named_prefix(self) -> None:
        cache.set("perm:u1:b1", "a", 60)
        cache.set("perm:u1:b2", "b", 60)
        cache.set("perm:u2:b1", "c", 60)
        cache.set("ops:demo-data:job:lock", "HELD", 60)

        deleted = delete_pattern("perm:u1:")

        assert deleted == 2
        assert cache.get("perm:u1:b1") is None
        assert cache.get("perm:u2:b1") == "c", "another user's cache survived"
        assert cache.get("ops:demo-data:job:lock") == "HELD", (
            "the unrelated coordination key survived — this line IS the bug fix"
        )

    def test_the_resolver_invalidation_spares_unrelated_keys(self, branch) -> None:
        """Through the real caller, not just the helper."""
        from apps.configuration import resolver
        from apps.configuration.registry import Scope

        cache.set("ops:demo-data:job:lock", "HELD", 60)

        resolver.set_value(
            "orders.default_type", "DINE_IN", scope=Scope.BRANCH, scope_id=branch.id
        )

        assert cache.get("ops:demo-data:job:lock") == "HELD"

    def test_the_permission_invalidation_spares_unrelated_keys(self, make_user) -> None:
        from apps.authz import services

        cache.set("ops:demo-data:job:lock", "HELD", 60)

        services.invalidate_all()

        assert cache.get("ops:demo-data:job:lock") == "HELD"

    def test_a_permission_change_still_lands(self, make_user, branch) -> None:
        """
        The half that must not regress the other way: invalidation that spares
        too much is a cashier keeping a permission that was just revoked.
        """
        from apps.authz import services

        user = make_user(role="CASHIER")
        first = services.effective_permissions(user.id, branch.id)
        assert "orders.create" in first

        from apps.authz.models import Role

        role = Role.objects.get(organization=user.organization, code="CASHIER")
        role.set_permissions(sorted(role.permission_codes - {"orders.create"}))

        assert "orders.create" not in services.effective_permissions(user.id, branch.id)
