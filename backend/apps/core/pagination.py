"""
Cursor pagination for unbounded collections.

Offset pagination degrades on large tables and — worse — skips or duplicates
rows when data is inserted while a client is scrolling. Orders, stock movements
and audit logs are all append-heavy, so cursors are the correct default.

Small bounded lists (categories, tables, payment methods) set
`pagination_class = None` and return everything.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import Any

from rest_framework.pagination import CursorPagination, PageNumberPagination
from rest_framework.response import Response


class EnvelopeCursorPagination(CursorPagination):
    page_size = 50
    max_page_size = 200
    page_size_query_param = "page_size"
    ordering = "-created_at"
    cursor_query_param = "cursor"

    def get_paginated_response(self, data: Any) -> Response:
        return Response(
            OrderedDict(
                [
                    ("success", True),
                    ("data", data),
                    (
                        "meta",
                        OrderedDict(
                            [
                                ("next", self.get_next_link()),
                                ("previous", self.get_previous_link()),
                                ("page_size", self.get_page_size(self.request)),
                            ]
                        ),
                    ),
                ]
            )
        )


class EnvelopePageNumberPagination(PageNumberPagination):
    """For report tables where a user genuinely needs a total count."""

    page_size = 50
    max_page_size = 500
    page_size_query_param = "page_size"

    def get_paginated_response(self, data: Any) -> Response:
        return Response(
            OrderedDict(
                [
                    ("success", True),
                    ("data", data),
                    (
                        "meta",
                        OrderedDict(
                            [
                                ("count", self.page.paginator.count),
                                ("page", self.page.number),
                                ("pages", self.page.paginator.num_pages),
                                ("next", self.get_next_link()),
                                ("previous", self.get_previous_link()),
                            ]
                        ),
                    ),
                ]
            )
        )
