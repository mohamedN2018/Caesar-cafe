"""
The brand, mirrored from the Web.

Qt stylesheets have no CSS variables, so the values live here as constants and
every stylesheet in the Desktop interpolates them. `tests/test_brand_parity.py`
parses `frontend/src/assets/brand.css` across the monorepo and fails if a value
here has drifted — the same discipline the vendored money modules follow, for
the same reason: two definitions of one fact eventually disagree, and the one
that disagrees is always the one nobody is looking at.

كافيه القيصر — burgundy and gold.
"""

from __future__ import annotations

# ── brand ────────────────────────────────────────────────────────────────────
# The two lightest steps are WARM, and were left behind when the Web warmed them
# in f14acc8. They are the steps used as a tint behind something, sitting on the
# cream surfaces (#fbf7f0, #f4ede1), and a neutral pink patch on cream reads as a
# different palette rather than a lighter one. See the note in brand.css.
#
# The parity guard caught this the moment somebody ran the Desktop suite — which
# is exactly what it is for, and also why it matters that it gets run: the drift
# had been sitting in the branch since that commit, invisible on the Web side
# because the Web was the half that was right.
BRAND_50 = "#fbf2ee"
BRAND_100 = "#f4dfd9"
BRAND_200 = "#f3c4c7"
BRAND_300 = "#e59aa0"
BRAND_400 = "#d06a74"
BRAND_500 = "#b3454f"
BRAND_600 = "#94303a"
BRAND_700 = "#7b1e28"
BRAND_800 = "#611720"
BRAND_900 = "#4a121a"

GOLD_100 = "#f7edcf"
GOLD_200 = "#eeda9f"
GOLD_300 = "#e0c268"
GOLD_400 = "#d3ae3f"
GOLD_500 = "#c9a227"
GOLD_600 = "#a8851d"
GOLD_700 = "#866818"

# ── surfaces ─────────────────────────────────────────────────────────────────
SURFACE = "#ffffff"
SURFACE_MUTED = "#fbf7f0"
SURFACE_SUNKEN = "#f4ede1"
BORDER = "#e7ddcc"
BORDER_STRONG = "#d3c4ab"

INK = "#2a1a16"
INK_MUTED = "#6b5a52"
INK_FAINT = "#9a8b83"
FG_ON_BRAND = "#ffffff"
FG_ON_GOLD = "#2a1a16"

# ── state ────────────────────────────────────────────────────────────────────
# Not brand-derived on purpose: a burgundy "danger" beside a burgundy header is
# a warning nobody sees, and these have to read from across a kitchen.
SUCCESS = "#2e7d4f"
SUCCESS_BG = "#eaf5ee"
WARNING = "#c77700"
WARNING_BG = "#fdf3e2"
DANGER = "#b3261e"
DANGER_BG = "#fceceb"
INFO = "#1f6f8b"
INFO_BG = "#e8f2f6"

# ── the room ─────────────────────────────────────────────────────────────────
FLOOR_TILE = "#efe6d6"
FLOOR_TILE_ALT = "#e8ddc9"
WOOD = "#a9713f"
WOOD_DARK = "#7d5029"
WOOD_EDGE = "#6a421f"
CHAIR = "#8a5a33"
CHAIR_OCCUPIED = "#7b1e28"
TABLE_FREE = "#ffffff"
TABLE_BUSY = "#f3c4c7"
TABLE_READY = "#cfe8da"
TABLE_LATE = "#f7d4d1"

#: name → value, for the parity test. Keys match the CSS custom properties with
#: the leading `--` removed.
TOKENS: dict[str, str] = {
    "brand-50": BRAND_50,
    "brand-100": BRAND_100,
    "brand-200": BRAND_200,
    "brand-300": BRAND_300,
    "brand-400": BRAND_400,
    "brand-500": BRAND_500,
    "brand-600": BRAND_600,
    "brand-700": BRAND_700,
    "brand-800": BRAND_800,
    "brand-900": BRAND_900,
    "gold-100": GOLD_100,
    "gold-200": GOLD_200,
    "gold-300": GOLD_300,
    "gold-400": GOLD_400,
    "gold-500": GOLD_500,
    "gold-600": GOLD_600,
    "gold-700": GOLD_700,
    "surface": SURFACE,
    "surface-muted": SURFACE_MUTED,
    "surface-sunken": SURFACE_SUNKEN,
    "border": BORDER,
    "border-strong": BORDER_STRONG,
    "ink": INK,
    "ink-muted": INK_MUTED,
    "ink-faint": INK_FAINT,
    "fg-on-brand": FG_ON_BRAND,
    "fg-on-gold": FG_ON_GOLD,
    "success": SUCCESS,
    "success-bg": SUCCESS_BG,
    "warning": WARNING,
    "warning-bg": WARNING_BG,
    "danger": DANGER,
    "danger-bg": DANGER_BG,
    "info": INFO,
    "info-bg": INFO_BG,
    "floor-tile": FLOOR_TILE,
    "floor-tile-alt": FLOOR_TILE_ALT,
    "wood": WOOD,
    "wood-dark": WOOD_DARK,
    "wood-edge": WOOD_EDGE,
    "chair": CHAIR,
    "chair-occupied": CHAIR_OCCUPIED,
    "table-free": TABLE_FREE,
    "table-busy": TABLE_BUSY,
    "table-ready": TABLE_READY,
    "table-late": TABLE_LATE,
}
