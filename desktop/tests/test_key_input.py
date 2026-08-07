"""
The licence key field.

This key arrives as a photo on a phone or a WhatsApp message, and activation is
rate-limited to five attempts per hour — so the field has to be forgiving about
how it is typed. These tests are the specification for "forgiving".
"""

from __future__ import annotations

import pytest
from PySide6.QtCore import Qt

from caesar_pos.ui.activation.key_input import LicenseKeyInput


@pytest.fixture
def field(qtbot) -> LicenseKeyInput:
    widget = LicenseKeyInput()
    qtbot.addWidget(widget)
    # Focus assertions need a shown, active window — even offscreen.
    widget.show()
    qtbot.waitExposed(widget)
    widget.activateWindow()
    return widget


class TestTyping:
    def test_typing_a_full_key_yields_the_canonical_form(self, field, qtbot) -> None:
        for char in "7X29K8P43F1A9WYZ":
            qtbot.keyClicks(field._fields[_active(field)], char)
        assert field.key() == "QSR-7X29-K8P4-3F1A-9WYZ"
        assert field.is_valid()

    def test_focus_advances_between_segments(self, field, qtbot) -> None:
        qtbot.keyClicks(field._fields[0], "7X29")
        assert field._fields[1].hasFocus()

    def test_lower_case_is_upper_cased_as_you_type(self, field, qtbot) -> None:
        qtbot.keyClicks(field._fields[0], "7x29")
        assert field._fields[0].text() == "7X29"

    @pytest.mark.parametrize(
        ("typed", "expected"),
        [("3fia", "3F1A"), ("3fla", "3F1A"), ("ox29", "0X29")],
    )
    def test_crockford_confusables_are_folded(self, field, qtbot, typed, expected) -> None:
        """I and L become 1, O becomes 0 — the characters people misread."""
        qtbot.keyClicks(field._fields[0], typed)
        assert field._fields[0].text() == expected

    def test_characters_outside_the_alphabet_are_rejected(self, field, qtbot) -> None:
        qtbot.keyClicks(field._fields[0], "7U!@")
        assert field._fields[0].text() == "7"

    def test_backspace_at_the_start_returns_to_the_previous_segment(self, field, qtbot) -> None:
        qtbot.keyClicks(field._fields[0], "7X29")
        assert field._fields[1].hasFocus()
        qtbot.keyClick(field._fields[1], Qt.Key.Key_Backspace)
        assert field._fields[0].hasFocus()
        assert field._fields[0].text() == "7X2"


class TestPaste:
    @pytest.mark.parametrize(
        "pasted",
        [
            "QSR-7X29-K8P4-3F1A-9WYZ",
            "qsr-7x29-k8p4-3f1a-9wyz",
            "QSR7X29K8P43F1A9WYZ",
            "  QSR-7X29-K8P4-3F1A-9WYZ  ",
            "7X29-K8P4-3F1A-9WYZ",
            "QSR 7X29 K8P4 3F1A 9WYZ",
        ],
    )
    def test_a_whole_key_spreads_across_the_segments(self, field, pasted: str) -> None:
        field.set_key(pasted)
        assert field.key() == "QSR-7X29-K8P4-3F1A-9WYZ"
        assert field.is_valid()

    def test_a_real_clipboard_paste_into_one_segment_spreads_out(self, field, qtbot) -> None:
        """
        Users paste wherever the cursor happens to be. Without the
        insertFromMimeData override, maxLength would silently truncate a full
        key to its first four characters.
        """
        from PySide6.QtGui import QGuiApplication

        QGuiApplication.clipboard().setText("QSR-7X29-K8P4-3F1A-9WYZ")
        field._fields[0].setFocus()
        field._fields[0].paste()

        assert field.key() == "QSR-7X29-K8P4-3F1A-9WYZ"
        assert field.is_valid()

    def test_typing_a_whole_key_into_one_segment_spreads_out(self, field) -> None:
        field._fields[0].setText("QSR7X29K8P43F1A9WYZ")
        assert field.key() == "QSR-7X29-K8P4-3F1A-9WYZ"


class TestCompletion:
    def test_incomplete_is_not_valid(self, field) -> None:
        field.set_key("7X29K8P4")
        assert not field.is_complete()
        assert not field.is_valid()

    def test_the_changed_signal_reports_completeness(self, field, qtbot) -> None:
        with qtbot.waitSignal(field.changed) as blocker:
            field.set_key("QSR-7X29-K8P4-3F1A-9WYZ")
        assert blocker.args == [True]

    def test_clear_resets_and_refocuses(self, field) -> None:
        field.set_key("QSR-7X29-K8P4-3F1A-9WYZ")
        field.clear()
        assert field.key() == "QSR----"
        assert not field.is_valid()
        assert field._fields[0].hasFocus()


class TestLayout:
    def test_the_key_reads_left_to_right_inside_an_rtl_app(self, field) -> None:
        """The key is Latin-alphanumeric; mirroring it would be unreadable."""
        for segment in field._fields:
            assert segment.layoutDirection() == Qt.LayoutDirection.LeftToRight

    def test_no_segment_ever_holds_more_than_four_characters(self, field) -> None:
        """
        The invariant, tested through behaviour rather than through maxLength —
        which the widget deliberately does not set, because Qt applies it before
        textChanged and would truncate a pasted key.
        """
        for typed in [
            "QSR-7X29-K8P4-3F1A-9WYZ",
            "7X29K8P43F1A9WYZEXTRA",
            "ABCDEFGH",
        ]:
            field._fields[0].setText(typed)
            assert all(len(s.text()) <= 4 for s in field._fields), typed

    def test_excess_beyond_a_full_key_is_discarded(self, field) -> None:
        field._fields[0].setText("7X29K8P43F1A9WYZ" + "ZZZZ")
        assert field.key() == "QSR-7X29-K8P4-3F1A-9WYZ"


def _active(field: LicenseKeyInput) -> int:
    """Index of the first segment that is not yet full."""
    for index, segment in enumerate(field._fields):
        if len(segment.text()) < 4:
            return index
    return len(field._fields) - 1
