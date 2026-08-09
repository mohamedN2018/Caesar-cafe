r"""
The branch's printers, defined once instead of on every terminal.

Until now a printer was a string typed into each Desktop and a `printer_name`
on each station. Three terminals meant three places to fix a typo, and the day
the receipt printer was replaced somebody had to walk to every till. Worse: the
name only existed on the machine that had it, so nothing on the server could
say what the cafe actually owned.

This is the registry. A printer is defined for the branch, synced to every
terminal like the catalogue is, and referenced by id — so replacing the hardware
is one edit in the Web Admin and the terminals follow at the next pull.

**The device path stays per-terminal.** `\\.\COM3` on the till by the door is
not the same port as on the one at the back, and pretending a branch-wide
registry knows a machine's serial ports would produce a system that is wrong on
two out of three terminals. So the registry holds WHAT the printer is and WHERE
it belongs in the workflow; each device keeps its own local binding.
"""

from __future__ import annotations

from django.db import models

from apps.core.models import SoftDeletableModel, TenantScopedModel

#: The two thermal rolls that exist in this market. Named once so the check
#: constraint and the serializer cannot drift into disagreeing.
PAPER_WIDTHS = (58, 80)


class PrinterKind(models.TextChoices):
    """
    What a printer is FOR, which is what decides where its jobs come from.

    Not a paper size — that is `paper_width_mm`. A cafe with one physical
    printer doing both receipts and kitchen tickets has one row of each kind
    pointing at the same device, because the two jobs are routed differently
    even when they land in the same place.
    """

    RECEIPT = "RECEIPT", "RECEIPT"
    KITCHEN = "KITCHEN", "KITCHEN"
    REPORT = "REPORT", "REPORT"


class ConnectionKind(models.TextChoices):
    """
    How a terminal reaches it.

    NETWORK printers are the only ones a branch-wide registry can fully
    describe: an IP is the same from every till. USB and WINDOWS are named here
    but bound locally, because a port is a property of a machine.
    """

    NETWORK = "NETWORK", "NETWORK"
    USB = "USB", "USB"
    WINDOWS = "WINDOWS", "WINDOWS"


class Printer(TenantScopedModel, SoftDeletableModel):
    name_ar = models.CharField(max_length=100, help_text="طابعة الكاشير / طابعة المطبخ")
    code = models.CharField(max_length=32, help_text="RECEIPT1, KITCHEN_HOT")
    kind = models.CharField(max_length=8, choices=PrinterKind.choices, default=PrinterKind.RECEIPT)
    connection = models.CharField(
        max_length=8, choices=ConnectionKind.choices, default=ConnectionKind.NETWORK
    )

    #: For NETWORK printers. Blank for USB/WINDOWS, where the terminal binds it.
    host = models.CharField(max_length=100, blank=True)
    port = models.PositiveIntegerField(default=9100)

    #: The Windows share or device path, when every terminal happens to use the
    #: same one. A terminal that has its own overrides this locally.
    device_path = models.CharField(max_length=200, blank=True)

    #: 80mm rolls are 576 dots at 203dpi; 58mm are 384. The Arabic rasteriser
    #: draws to this width, so getting it wrong produces a receipt with the
    #: right words in the wrong place — see `printing/arabic.py` on the Desktop.
    paper_width_mm = models.PositiveSmallIntegerField(default=80)

    copies = models.PositiveSmallIntegerField(default=1)
    cut_after = models.BooleanField(default=True)

    #: Which kitchen stations print here. A station with no printer shows on the
    #: KDS and prints nowhere, which is a legitimate setup and not an error.
    stations = models.ManyToManyField("kitchen.Station", blank=True, related_name="printers")

    is_default = models.BooleanField(
        default=False, help_text="Where a job of this kind goes when nothing else claims it."
    )

    class Meta:
        db_table = "printers"
        ordering = ["kind", "name_ar"]
        constraints = [
            models.UniqueConstraint(fields=["branch", "code"], name="uniq_printer_code_per_branch"),
            models.UniqueConstraint(
                fields=["branch", "kind"],
                condition=models.Q(is_default=True, is_active=True),
                name="one_default_printer_per_kind",
            ),
            models.CheckConstraint(
                # 58mm and 80mm are the two rolls that exist in this market. A
                # number outside that is a typo, and it silently produces
                # unreadable Arabic rather than an error.
                condition=models.Q(paper_width_mm__in=PAPER_WIDTHS),
                name="printer_paper_is_a_real_roll",
            ),
        ]

    def __str__(self) -> str:
        return f"{self.name_ar} ({self.code})"

    @property
    def dots(self) -> int:
        """Printable width in dots at 203dpi — what the rasteriser needs."""
        return 576 if self.paper_width_mm == 80 else 384
