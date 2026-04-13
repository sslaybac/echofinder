"""Custom item delegate that renders the four-slot indicator system.

Layout (left to right within each tree row):
  [Qt expand arrow]  [slot 1: type icon]  filename  [slot 2]  [slot 3]  [slot 4]

Slots 1–4 are always allocated.  Slots 2–4 are drawn right-aligned; their space
is always reserved even when empty, so the filename column width is stable as
indicator icons appear and disappear.

Slot roles are defined here as module-level constants so that the model and
delegate share a single definition without a circular import.
"""

from __future__ import annotations

from PyQt6.QtCore import QRect, Qt
from PyQt6.QtGui import QPainter
from PyQt6.QtWidgets import QStyle, QStyleOptionViewItem, QStyledItemDelegate

# Custom item data roles for slot icons (QIcon | None)
SLOT2_ROLE = Qt.ItemDataRole.UserRole + 1  # ownership
SLOT3_ROLE = Qt.ItemDataRole.UserRole + 2  # permissions
SLOT4_ROLE = Qt.ItemDataRole.UserRole + 3  # hashing / duplicate state

_SLOT_SIZE = 16        # pixels per slot icon
_SLOT_SPACING = 2      # pixels between adjacent slot icons
_RIGHT_PADDING = 4     # padding between the last slot and the right edge

# Total pixels reserved on the right for slots 2, 3, 4:
#   3 × 16 + 2 × 2 (gaps between slots) + 4 (right edge) = 56
_RIGHT_RESERVE = 3 * _SLOT_SIZE + 2 * _SLOT_SPACING + _RIGHT_PADDING


class NodeIndicatorDelegate(QStyledItemDelegate):
    """Renders tree items with the four-slot indicator system.

    The base class handles the selection background, focus rectangle, type
    icon (slot 1 via DecorationRole), and the filename text — all within a
    rect that is narrowed to leave room for the right-side slots.  This
    delegate then draws slots 2–4 in the reserved right strip.
    """

    def paint(
        self,
        painter: QPainter,
        option: QStyleOptionViewItem,
        index,
    ) -> None:
        """Paint one tree row with the four-slot indicator system.

        Narrows the base-class rect so that Qt draws the type icon (slot 1)
        and filename within the left portion, then manually draws slots 2–4
        right-aligned in the reserved strip.

        Args:
            painter: Active ``QPainter`` for the viewport.
            option: Style and geometry information for this row.
            index: ``QModelIndex`` of the item being painted.
        """
        # Narrow option.rect so that the base class draws text and slot-1 icon
        # within the left portion, leaving the right strip clear for our icons.
        original_rect = QRect(option.rect)
        option.rect = original_rect.adjusted(0, 0, -_RIGHT_RESERVE, 0)
        super().paint(painter, option, index)
        option.rect = original_rect  # restore for icon drawing below

        # Retrieve slot icons from the model (None = slot is empty)
        s2 = index.data(SLOT2_ROLE)
        s3 = index.data(SLOT3_ROLE)
        s4 = index.data(SLOT4_ROLE)

        # Vertical centre of the row, sized to the icon height
        y = original_rect.top() + (original_rect.height() - _SLOT_SIZE) // 2

        # Draw slots right-to-left: slot 4 is rightmost, slot 2 is leftmost
        # of the three right-side slots.
        slot_icons = [s4, s3, s2]  # rightmost first
        x = original_rect.right() - _RIGHT_PADDING - _SLOT_SIZE
        for icon in slot_icons:
            if icon is not None and not icon.isNull():
                icon.paint(
                    painter,
                    QRect(x, y, _SLOT_SIZE, _SLOT_SIZE),
                    Qt.AlignmentFlag.AlignCenter,
                )
            x -= _SLOT_SIZE + _SLOT_SPACING

    def sizeHint(self, option: QStyleOptionViewItem, index) -> "QSize":  # type: ignore[override]
        """Return the preferred size for *index*, widened for the slot strip.

        Args:
            option: Style options for the item.
            index: ``QModelIndex`` of the item.

        Returns:
            A ``QSize`` with extra width reserved for the three right-side slots.
        """
        hint = super().sizeHint(option, index)
        # Ensure items are wide enough to show all slots without clipping
        hint.setWidth(hint.width() + _RIGHT_RESERVE)
        return hint
