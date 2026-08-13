"""
Orders on the terminal.

The same shape as the server: an append-only event stream folded into an
aggregate. Not because symmetry is elegant, but because it is the only way the
two can agree — the Desktop folds the events it produced, the server folds the
same events after they sync, and both run the same `money.py`.

Anything else would mean two implementations of "what does this bill come to",
and they only stay equal until the first bug fix lands in one of them.
"""

from .fold import FoldedOrder, fold  # noqa: F401
