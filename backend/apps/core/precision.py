"""
The decimal shapes every money and quantity column is declared with.

These five dicts were copy-pasted into ten `models.py` files. Nothing kept them
in step, so a `MONEY` that gained a digit in one app and not another would be a
column that silently truncates a total — and the failure surfaces as a customer
charged the wrong amount, weeks later, in one app and not the rest.

They also produce **531 of the backend's mypy errors** on their own. A bare dict
literal is inferred as `dict[str, int]`, and `DecimalField(**that)` is then an
error at every single field declaration in the project. Typing it here fixes all
of them at once, and turns `mypy` back into a check that can be read.

`Decimal`, never binary floating point — see docs/02. The architecture guard in
`tests/test_architecture_guards.py` refuses the float column type outright, by
grepping for its name; these constants are the other half of that rule,
describing what a `DecimalField` is allowed to look like.

(That guard is a plain text search, which is why the sentence above talks around
the name rather than using it. Bluntness is the point: an AST check would miss a
field built dynamically or named in a string, and this rule is worth catching
every way somebody could write it — including in a docstring, as it just did.)

**Two profiles for each, and the difference is deliberate:**

  * `MONEY` (12,2) is a figure somebody is charged — a line, an order, a payment.
    Ten digits before the point is 99 million EGP on one order.
  * `WIDE_MONEY` (14,2) is a figure somebody is *reported*. A rollup sums a
    quarter's trading, and a quarter of orders overflows the shape of one order.

  * `QUANTITY` (14,3) is stock: grams of coffee, litres of milk, a delivery.
  * `LINE_QUANTITY` (10,3) is how many of something is on one order line. Nobody
    orders ten million cappuccinos, and the narrower column says so.
"""

from __future__ import annotations

from typing import Final, TypedDict


class DecimalSpec(TypedDict):
    """The keyword arguments `models.DecimalField` takes for precision."""

    max_digits: int
    decimal_places: int


#: What a customer is charged.
MONEY: Final[DecimalSpec] = {"max_digits": 12, "decimal_places": 2}

#: What a report sums. Wider, because a quarter of orders will not fit in the
#: shape of a single order.
WIDE_MONEY: Final[DecimalSpec] = {"max_digits": 14, "decimal_places": 2}

#: Stock, in the item's own unit. Three decimals because a recipe uses 18g of
#: beans and 150ml of milk, and two would lose grams on every drink.
QUANTITY: Final[DecimalSpec] = {"max_digits": 14, "decimal_places": 3}

#: A unit cost, at FOUR decimals. Cost per gram is fractions of a piaster, and
#: rounding it to two corrupts COGS on every recipe. This is the one place in
#: the system where a stored figure is deliberately finer than the currency.
UNIT_COST: Final[DecimalSpec] = {"max_digits": 12, "decimal_places": 4}

#: How many of something is on one order line.
LINE_QUANTITY: Final[DecimalSpec] = {"max_digits": 10, "decimal_places": 3}

#: A percentage: VAT, service, a discount. Three digits before the point leaves
#: room for a value above 100 to be *stored* and then rejected by a validator
#: with a message, rather than raising a database error nobody can read.
PERCENT: Final[DecimalSpec] = {"max_digits": 5, "decimal_places": 2}
