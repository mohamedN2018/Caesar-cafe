"""
Rendering Arabic for a thermal printer.

Thermal printers cannot do this themselves. Their built-in code pages have
isolated Arabic letterforms and no shaping engine, so sending `"كابتشينو"` as
text produces disconnected letters in the wrong order — legible to nobody, and
the customer's receipt is the one artefact of this system they take home.

So the terminal does the typography and sends a **bitmap**:

    text → reshape (join the letters) → bidi (visual order) → PIL → raster

Two steps, both required, and each useless without the other. Reshaping picks the
initial/medial/final form of every letter; the bidi pass reorders the run for a
device that draws strictly left to right. Skip the first and you get isolated
forms; skip the second and the words come out backwards.

Latin and digits inside an Arabic line are handled by the bidi algorithm, which
is why `"كابتشينو ×2"` comes out with the 2 in the right place.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

#: 80mm paper at 203 dpi. The near-universal receipt printer.
PAPER_WIDTH_PX = 576
#: 58mm, the other common roll.
NARROW_WIDTH_PX = 384

DEFAULT_FONT_CANDIDATES = (
    # Shipped with the installer. Amiri and Cairo both shape Arabic correctly;
    # DejaVu is the fallback that at least draws the glyphs.
    "assets/fonts/Amiri-Regular.ttf",
    "assets/fonts/Cairo-Regular.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def shape(text: str) -> str:
    """
    Reshape and reorder one line for visual rendering.

    Returns the text unchanged if the shaping libraries are missing rather than
    raising — a receipt with disconnected letters is bad; a POS that cannot print
    at all because a font library is absent is worse.
    """
    try:
        import arabic_reshaper
        from bidi.algorithm import get_display
    except ImportError:  # pragma: no cover - both are hard dependencies
        logger.error("Arabic shaping libraries missing; receipt text will be malformed")
        return text

    return get_display(arabic_reshaper.reshape(text))


def has_arabic(text: str) -> bool:
    """Arabic, Arabic Supplement, and Arabic Presentation Forms."""
    return any("؀" <= ch <= "ۿ" or "ﭐ" <= ch <= "﻿" for ch in text)


@dataclass(frozen=True)
class RenderOptions:
    width: int = PAPER_WIDTH_PX
    font_size: int = 24
    line_spacing: int = 6
    margin: int = 8
    font_path: str | None = None


def find_font(options: RenderOptions | None = None) -> str | None:
    options = options or RenderOptions()
    candidates = (
        [options.font_path, *DEFAULT_FONT_CANDIDATES]
        if options.font_path
        else DEFAULT_FONT_CANDIDATES
    )
    for candidate in candidates:
        if candidate and Path(candidate).exists():
            return candidate
    return None


def render(lines: list[str], options: RenderOptions | None = None):
    """
    Draw the lines into a 1-bit PIL image, ready for an ESC/POS raster command.

    Alignment is right-to-left by default because the document is Arabic. A line
    that is entirely Latin — a serial number, a barcode payload — is left as the
    caller placed it.
    """
    from PIL import Image, ImageDraw, ImageFont

    options = options or RenderOptions()
    font_path = find_font(options)
    font = (
        ImageFont.truetype(font_path, options.font_size) if font_path else ImageFont.load_default()
    )

    prepared = [shape(line) if has_arabic(line) else line for line in lines]

    line_height = options.font_size + options.line_spacing
    height = max(line_height, len(prepared) * line_height + options.margin * 2)

    # Mode "1": one bit per pixel, which is exactly what the printer wants and
    # keeps the payload small enough to send over a slow serial link.
    image = Image.new("1", (options.width, height), color=1)
    draw = ImageDraw.Draw(image)

    y = options.margin
    for original, text in zip(lines, prepared, strict=True):
        if has_arabic(original):
            width = int(draw.textlength(text, font=font))
            x = options.width - width - options.margin
        else:
            x = options.margin

        draw.text((x, y), text, font=font, fill=0)
        y += line_height

    return image


def render_centered(text: str, options: RenderOptions | None = None):
    from PIL import Image, ImageDraw, ImageFont

    options = options or RenderOptions()
    font_path = find_font(options)
    font = (
        ImageFont.truetype(font_path, options.font_size) if font_path else ImageFont.load_default()
    )

    prepared = shape(text) if has_arabic(text) else text
    height = options.font_size + options.line_spacing + options.margin * 2

    image = Image.new("1", (options.width, height), color=1)
    draw = ImageDraw.Draw(image)
    width = int(draw.textlength(prepared, font=font))
    draw.text(((options.width - width) // 2, options.margin), prepared, font=font, fill=0)

    return image
