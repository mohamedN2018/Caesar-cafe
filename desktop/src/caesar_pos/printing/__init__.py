"""
Printing.

The receipt is the one artefact of this system a customer takes home, and in
Egypt it is also a tax document — so it is built once, from the folded order,
and rendered identically to a printer, a preview and a reprint.

Arabic is rasterised here rather than sent as text: thermal printers have no
shaping engine, and `"كابتشينو"` sent as characters comes out as disconnected
letters in the wrong order.
"""

from .arabic import render, shape  # noqa: F401
from .receipt import Receipt, ReceiptHeader, build, build_kitchen_ticket  # noqa: F401
from .spooler import EscposPrinter, drain, enqueue  # noqa: F401
