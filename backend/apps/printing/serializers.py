from __future__ import annotations

from rest_framework import serializers

from apps.authz.drf import auth_context

from .models import PAPER_WIDTHS, Printer


class PrinterSerializer(serializers.ModelSerializer):
    station_names = serializers.SerializerMethodField()
    dots = serializers.IntegerField(read_only=True)

    class Meta:
        model = Printer
        fields = [
            "id",
            "name_ar",
            "code",
            "kind",
            "connection",
            "host",
            "port",
            "device_path",
            "paper_width_mm",
            "dots",
            "copies",
            "cut_after",
            "stations",
            "station_names",
            "is_default",
            "is_active",
        ]
        read_only_fields = ["id", "dots", "station_names"]

    def get_station_names(self, printer: Printer) -> list[str]:
        return [station.name_ar for station in printer.stations.all()]

    def validate_paper_width_mm(self, value: int) -> int:
        """
        58mm and 80mm are the rolls that exist.

        The database says the same thing, but a check constraint reaching the
        caller is a 500 — and the person filling in this form deserves to be
        told which numbers are allowed.
        """
        if value not in PAPER_WIDTHS:
            raise serializers.ValidationError("عرض الورق يجب أن يكون 58 أو 80 مم.")
        return value

    def validate_code(self, value: str) -> str:
        """
        Codes are how a terminal names a printer in a log line, so two printers
        sharing one in a branch makes those lines ambiguous forever.
        """
        principal = auth_context(self.context["request"])
        clash = Printer.all_objects.filter(branch_id=principal.branch_id, code=value)
        if self.instance:
            clash = clash.exclude(pk=self.instance.pk)

        if clash.exists():
            raise serializers.ValidationError("يوجد طابعة بنفس الكود في هذا الفرع.")
        return value

    def validate(self, attrs: dict) -> dict:
        """
        A network printer without a host is a printer nothing can reach.

        Caught here rather than at print time, because the failure at print time
        is a receipt that never appears and a cashier looking at the machine.
        """
        connection = attrs.get("connection", getattr(self.instance, "connection", None))
        host = attrs.get("host", getattr(self.instance, "host", ""))

        if connection == "NETWORK" and not host:
            raise serializers.ValidationError({"host": "طابعة الشبكة تحتاج عنوان IP."})
        return attrs
