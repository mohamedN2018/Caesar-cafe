"""
Repair orders written with `TAKEAWAY` instead of `TAKE_AWAY`.

`seed_demo` chose the channel from a list of string literals, one of which was
missing its underscore. Nothing rejected it: `order_type` is a CharField with
choices, and Django does not enforce choices on `.create()` — so a quarter of
every seeded day carried a channel that matches no `VariantChannelPrice`, groups
under no channel in a report, and renders in the SPA as the raw word TAKEAWAY.

Found by the check that now refuses a channel the branch has not enabled, which
is the first thing that ever compared that string against the real set.

The seed is fixed to use the enum. This repairs what it already wrote. Safe on
any database: `TAKEAWAY` was never a value the product could produce by any other
route, so there is nothing here that could be somebody's real, different data.
"""

from django.db import migrations


def repair(apps, schema_editor):
    Order = apps.get_model("orders", "Order")
    Order.objects.filter(order_type="TAKEAWAY").update(order_type="TAKE_AWAY")


def unrepair(apps, schema_editor):
    """
    Deliberately a no-op.

    Reversing would put a value back that no code path accepts, and there is no
    way to tell a repaired row from one that was always correct. Leaving them
    right is the honest reverse.
    """


class Migration(migrations.Migration):
    dependencies = [
        ("orders", "0005_external_order_channel"),
    ]

    operations = [
        migrations.RunPython(repair, unrepair),
    ]
