"""
The recycle bin: what was deleted, and putting it back.

Deleting in this product means deactivating — `is_active = False` — because a
product that has ever been sold must not be removable. That rule is right, and it
left a hole: **deactivated rows became invisible.** A category switched off by
accident vanished from every screen, still in the database, with no route back
that did not involve a shell.

The first test here is the one that matters most, and it exists because the first
version shipped a wrong path and 500ed the screen: every entry in the registry
declares how to reach its organisation, those paths are hand-written, and a wrong
one is a `FieldError` at request time on the one screen somebody opens *because*
something has already gone wrong.
"""

from __future__ import annotations

import pytest

from apps.core.recycle import BY_KEY, RECYCLABLE, deleted_rows, describe, restore

pytestmark = pytest.mark.django_db


class TestTheRegistryIsWiredCorrectly:
    def test_every_organization_path_resolves(self, organization) -> None:
        """
        Each declared path must be a real lookup.

        Django does not validate a filter keyword until the query runs, so a typo
        sits silently until somebody opens the bin — and they open it *after*
        deleting something by mistake, which is the worst possible moment to meet
        a 500. Running each query proves the path.
        """
        broken = []
        for entry in RECYCLABLE:
            try:
                list(deleted_rows(entry, organization_id=organization.id)[:1])
            except Exception as exc:  # the point is to collect every failure, not the first
                broken.append(f"{entry.key}: {type(exc).__name__}: {exc}")

        assert not broken, "these entries cannot be queried:\n" + "\n".join(broken)

    def test_every_title_field_exists_on_its_model(self) -> None:
        # A missing title field silently falls back to `str(row)`, which for most
        # models is a UUID — an operator cannot restore what they cannot identify.
        missing = []
        for entry in RECYCLABLE:
            fields = {f.name for f in entry.model_class()._meta.get_fields()}
            if entry.title_field not in fields:
                missing.append(f"{entry.key}.{entry.title_field}")

        assert not missing, f"title fields that do not exist: {missing}"

    def test_every_entry_is_soft_deletable(self) -> None:
        """
        A model without `is_active` cannot be in the bin.

        It would filter on a field it does not have, and `restore` would write
        two attributes onto a row that has no meaning for them.
        """
        wrong = [
            entry.key
            for entry in RECYCLABLE
            if not {"is_active", "deactivated_at"}
            <= {f.name for f in entry.model_class()._meta.get_fields()}
        ]

        assert not wrong, f"not soft-deletable: {wrong}"

    def test_the_registry_is_not_empty_and_keys_are_unique(self) -> None:
        # A guard that matches nothing passes forever.
        keys = [entry.key for entry in RECYCLABLE]

        assert len(keys) >= 10
        assert len(keys) == len(set(keys))
        assert set(BY_KEY) == set(keys)


class TestDeletingThenRestoring:
    def test_a_deactivated_row_appears_in_the_bin(self, organization, branch) -> None:
        from apps.catalog.models import Category

        category = Category.objects.create(
            organization=organization, branch=branch, name_ar="حلويات"
        )
        entry = BY_KEY["catalog.Category"]
        assert not deleted_rows(entry, organization_id=organization.id).exists()

        category.is_active = False
        category.save(update_fields=["is_active"])

        rows = list(deleted_rows(entry, organization_id=organization.id))
        assert [row.pk for row in rows] == [category.pk]

    def test_restoring_brings_it_back_and_clears_the_stamp(self, organization, branch) -> None:
        from django.utils import timezone

        from apps.catalog.models import Category

        category = Category.objects.create(
            organization=organization,
            branch=branch,
            name_ar="حلويات",
            is_active=False,
            deactivated_at=timezone.now(),
        )
        entry = BY_KEY["catalog.Category"]

        restore(entry, category)
        category.refresh_from_db()

        assert category.is_active is True
        assert category.deactivated_at is None
        assert not deleted_rows(entry, organization_id=organization.id).exists()

    def test_the_bin_is_scoped_to_one_organisation(
        self, organization, other_organization, other_branch
    ) -> None:
        """
        The one mistake here that is not recoverable by deleting again.

        Every other error in this module can be undone; restoring across tenants
        puts somebody else's row back into their live catalogue.
        """
        from apps.catalog.models import Category

        Category.objects.create(
            organization=other_organization,
            branch=other_branch,
            name_ar="ليست لنا",
            is_active=False,
        )
        entry = BY_KEY["catalog.Category"]

        assert not deleted_rows(entry, organization_id=organization.id).exists()

    def test_a_row_is_described_by_something_a_person_recognises(
        self, organization, branch
    ) -> None:
        from apps.catalog.models import Category

        category = Category.objects.create(
            organization=organization, branch=branch, name_ar="حلويات", is_active=False
        )

        described = describe(BY_KEY["catalog.Category"], category)

        assert described["title"] == "حلويات"
        assert described["kind"] == "catalog.Category"
        assert described["kind_label"] == "الأقسام"
