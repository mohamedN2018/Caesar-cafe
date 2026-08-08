from __future__ import annotations

from rest_framework import serializers

from .models import Recipe, RecipeLine


class RecipeLineSerializer(serializers.ModelSerializer):
    item_code = serializers.CharField(source="item.code", read_only=True)
    item_name = serializers.CharField(source="item.name_ar", read_only=True)
    unit_code = serializers.CharField(source="unit.code", read_only=True)
    effective_quantity = serializers.DecimalField(
        max_digits=14,
        decimal_places=3,
        read_only=True,
        help_text="Including expected waste — what actually leaves the shelf.",
    )

    class Meta:
        model = RecipeLine
        fields = [
            "id",
            "item",
            "item_code",
            "item_name",
            "unit",
            "unit_code",
            "quantity",
            "waste_percent",
            "effective_quantity",
            "is_optional",
        ]
        read_only_fields = ["id"]


class RecipeSerializer(serializers.ModelSerializer):
    lines = RecipeLineSerializer(many=True)
    variant_name = serializers.CharField(source="variant.__str__", read_only=True)

    class Meta:
        model = Recipe
        fields = ["id", "variant", "variant_name", "yield_quantity", "notes", "is_active", "lines"]
        read_only_fields = ["id"]

    def create(self, validated_data: dict) -> Recipe:
        lines = validated_data.pop("lines", [])
        recipe = Recipe.objects.create(**validated_data)
        RecipeLine.objects.bulk_create(RecipeLine(recipe=recipe, **line) for line in lines)
        return recipe

    def update(self, instance: Recipe, validated_data: dict) -> Recipe:
        lines = validated_data.pop("lines", None)
        for field, value in validated_data.items():
            setattr(instance, field, value)
        instance.save()

        if lines is not None:
            instance.lines.all().delete()
            RecipeLine.objects.bulk_create(RecipeLine(recipe=instance, **line) for line in lines)
        return instance


class CostLineSerializer(serializers.Serializer):
    item_code = serializers.CharField()
    item_name = serializers.CharField()
    quantity = serializers.DecimalField(max_digits=14, decimal_places=3)
    unit_code = serializers.CharField()
    unit_cost = serializers.DecimalField(max_digits=12, decimal_places=4)
    line_cost = serializers.DecimalField(max_digits=12, decimal_places=4)


class RecipeCostSerializer(serializers.Serializer):
    """
    What one portion costs, and — separately — what the figure does not include.

    `missing_costs` is not a warning bolted on: an item that has never been
    received has no cost, and a margin that looks excellent because an
    ingredient is silently contributing zero is worse than no margin at all.
    """

    total = serializers.DecimalField(max_digits=12, decimal_places=2)
    lines = CostLineSerializer(many=True)
    missing_costs = serializers.ListField(child=serializers.CharField())
    price = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    margin = serializers.DecimalField(max_digits=12, decimal_places=2, allow_null=True)
    margin_percent = serializers.DecimalField(max_digits=6, decimal_places=2, allow_null=True)
