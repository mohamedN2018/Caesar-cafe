"""
The local SQLite store.

Two kinds of table, and the prefix is a constant reminder of direction:

    m_*   MIRROR — pull-only, server-authoritative, replaced not merged.
          The UI never writes one. A price is a server fact that arrives; this
          terminal has no authority to alter it.

    l_*   LOCAL — written here first, then pushed. Orders, payments, shifts.

Mixing the two is how a cache quietly becomes a second source of truth, and the
first symptom is a terminal charging a price no manager set.
"""

from .db import Database, connect, transaction  # noqa: F401
