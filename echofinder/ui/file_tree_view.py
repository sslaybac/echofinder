"""FileTreeView — QTreeView with file operations for Stage 6.

New in Stage 6:
  - RenameDelegate: extends NodeIndicatorDelegate to support inline editing
    with collision detection, conflict-icon wiring, and status-bar messaging.
    Also paints the movement-mode ghost-source row as invisible.
  - _GhostOverlay: transparent child widget drawn on top of the viewport to
    render the ghost and dashed outline during keyboard movement mode.
  - FileTreeView: adds drag-and-drop, keyboard movement mode, rename mode,
    deletion flow, and a fully wired context menu.
"""

from __future__ import annotations

import sys
from pathlib import Path

from PyQt6.QtCore import (
    QAbstractItemModel,
    QEvent,
    QMimeData,
    QModelIndex,
    QPersistentModelIndex,
    QPoint,
    QRect,
    Qt,
    QTimer,
    pyqtSignal,
)
from PyQt6.QtGui import (
    QColor,
    QDrag,
    QKeyEvent,
    QPainter,
    QPen,
)
from PyQt6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QSizePolicy,
    QStyleOptionViewItem,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from echofinder.models.file_node import FileNode, HashState
from echofinder.services.file_operations import (
    ConflictAction,
    MoveResult,
    check_name_collision,
    delete_item,
    move_item,
    rename_item,
)
from echofinder.ui.node_delegate import NodeIndicatorDelegate
from echofinder.ui.tree_model import FileTreeModel

# MIME type used to carry the source path during an internal drag.
_DRAG_MIME_TYPE = "application/x-echofinder-path"


# ---------------------------------------------------------------------------
# Ghost overlay widget
# ---------------------------------------------------------------------------

class _GhostOverlay(QWidget):
    """Transparent overlay that draws the movement-mode ghost."""

    def __init__(self, parent: QWidget) -> None:
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAttribute(Qt.WidgetAttribute.WA_NoSystemBackground)
        self.setAutoFillBackground(False)
        self._rect: QRect | None = None
        self._collision: bool = False
        self.hide()

    def show_ghost(self, rect: QRect, collision: bool = False) -> None:
        """Show the ghost at *rect* with a blue (normal) or red (collision) outline.

        Args:
            rect: The bounding rectangle to highlight, in viewport coordinates.
            collision: When ``True``, draw a red outline to signal a merge conflict.
        """
        self._rect = QRect(rect)
        self._collision = collision
        # QAbstractItemView::scrollContentsBy calls viewport()->scroll(dx, dy),
        # which physically moves all child widgets of the viewport (including this
        # overlay) by the scroll delta.  Resetting to (0, 0) before each paint
        # counteracts any accumulated drift.
        self.move(0, 0)
        self.resize(self.parent().size())  # type: ignore[union-attr]
        self.show()
        self.raise_()
        self.update()

    def clear_ghost(self) -> None:
        """Hide the ghost overlay and clear the stored rect."""
        self._rect = None
        self.hide()
        self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        """Draw a semi-transparent fill and dashed outline at the ghost rect."""
        if self._rect is None:
            return
        painter = QPainter(self)
        # Semi-transparent blue fill
        painter.setOpacity(0.22)
        painter.fillRect(self._rect, QColor(80, 120, 200))
        painter.setOpacity(1.0)
        # Dashed outline: red on collision, blue otherwise
        color = QColor(200, 60, 60) if self._collision else QColor(60, 100, 200)
        pen = QPen(color, 2, Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(self._rect.adjusted(1, 1, -2, -2))
        painter.end()


# ---------------------------------------------------------------------------
# RenameDelegate
# ---------------------------------------------------------------------------

class RenameDelegate(NodeIndicatorDelegate):
    """NodeIndicatorDelegate extended with inline editing support.

    Also paints the movement-mode ghost-source row as invisible (the row is
    kept in the model for correct navigation, but the delegate skips painting
    it so the ghost overlay is the only visual representation).
    """

    def __init__(self, view: "FileTreeView", parent=None) -> None:
        super().__init__(parent)
        self._view = view
        # State set during a rename session
        self._conflict_node: FileNode | None = None
        self._editing: bool = False
        self._current_editor: QLineEdit | None = None
        self._rename_index: QPersistentModelIndex | None = None
        # Stores (old_path, new_path) after a successful rename; applied in
        # destroyEditor once the editor is fully torn down.
        self._pending_rename: tuple[Path, Path] | None = None
        # Set during movement mode to suppress painting of the source row
        self._hidden_persistent: QPersistentModelIndex | None = None

    # ------------------------------------------------------------------
    # Movement-mode row concealment
    # ------------------------------------------------------------------

    def set_hidden_source(self, index: QModelIndex | None) -> None:
        """Suppress painting of the row at *index* during keyboard movement mode.

        The row remains in the model for navigation purposes; only the visual
        rendering is suppressed so the ghost overlay is the sole representation.

        Args:
            index: The source row to hide, or ``None`` to stop hiding.
        """
        self._hidden_persistent = (
            QPersistentModelIndex(index) if (index is not None and index.isValid()) else None
        )

    # ------------------------------------------------------------------
    # Painting — conceal the ghost-source row during movement mode
    # ------------------------------------------------------------------

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        """Paint the row, skipping it entirely if it is the concealed ghost source.

        Args:
            painter: Active ``QPainter`` for the viewport.
            option: Style and geometry for this row.
            index: ``QModelIndex`` of the item to paint.
        """
        if self._hidden_persistent is not None:
            hidden = QModelIndex(self._hidden_persistent)
            if hidden.isValid() and index == hidden:
                return  # row is concealed; ghost overlay paints it instead
        super().paint(painter, option, index)

    # ------------------------------------------------------------------
    # Editor lifecycle
    # ------------------------------------------------------------------

    @property
    def is_editing(self) -> bool:
        return self._editing

    def createEditor(
        self, parent: QWidget, option: QStyleOptionViewItem, index: QModelIndex
    ) -> QWidget:
        """Create a ``QLineEdit`` inline editor and set the editing flag.

        Args:
            parent: The viewport widget that parents the editor.
            option: Style information for the row being edited.
            index: The model index of the item being renamed.

        Returns:
            A ``QLineEdit`` pre-populated by ``setEditorData``.
        """
        editor = QLineEdit(parent)
        self._editing = True
        self._current_editor = editor
        self._rename_index = QPersistentModelIndex(index)
        return editor

    def setEditorData(self, editor: QWidget, index: QModelIndex) -> None:
        """Pre-populate the editor with the current filename and select all text.

        Args:
            editor: The ``QLineEdit`` returned by ``createEditor``.
            index: The model index of the item being edited.
        """
        node: FileNode = index.internalPointer()
        le = editor  # type: ignore[assignment]
        le.setText(node.name)
        le.selectAll()

    def setModelData(
        self, editor: QWidget, model: QAbstractItemModel, index: QModelIndex
    ) -> None:
        """No-op: rename logic is handled entirely in ``eventFilter``."""
        # Handled entirely in eventFilter; this is intentionally a no-op.
        pass

    def destroyEditor(self, editor: QWidget, index: QModelIndex) -> None:
        """Tear down the editor and apply any pending rename after it is fully closed.

        Model and cache updates are deferred via ``QTimer.singleShot(0, ...)``
        so that editor teardown and any residual key events complete first.

        Args:
            editor: The editor widget to destroy.
            index: The model index that was being edited.
        """
        pending = self._pending_rename
        self._pending_rename = None
        self._exit_rename()
        super().destroyEditor(editor, index)
        if pending is not None:
            old_path, new_path = pending
            # Defer model/cache updates to the next event loop iteration so
            # that the editor teardown and any residual key events are fully
            # processed before we remove and re-insert rows.
            QTimer.singleShot(0, lambda: self._apply_pending_rename(old_path, new_path))

    def _apply_pending_rename(self, old_path: Path, new_path: Path) -> None:
        """Apply model and cache updates after the rename editor has closed."""
        if self._view._cache is not None:
            self._view._cache.update_path(str(old_path), str(new_path))
        model = self._view.model()
        if isinstance(model, FileTreeModel):
            model.notify_path_changed(str(old_path), str(new_path))
            expanded = self._view._collect_all_expanded()
            model.refresh_dir(old_path.parent)
            self._view._restore_all_expanded(expanded)
            # Re-select the renamed item so the preview pane stays populated.
            new_idx = model.index_for_path(new_path)
            if new_idx.isValid():
                self._view.setCurrentIndex(new_idx)

    # ------------------------------------------------------------------
    # Event filter — intercept Enter/Escape on the inline editor
    # ------------------------------------------------------------------

    def eventFilter(self, obj: object, event: QEvent) -> bool:
        """Intercept Enter (commit) and Escape (cancel) on the inline editor.

        Args:
            obj: The object the event was sent to.
            event: The event to filter.

        Returns:
            ``True`` if the event was consumed, otherwise defers to super.
        """
        if not isinstance(event, QKeyEvent):
            return super().eventFilter(obj, event)
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._try_commit()
            return True
        if event.key() == Qt.Key.Key_Escape:
            self._exit_rename()
            editor = self._current_editor
            if editor is not None:
                self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.RevertModelData)
            return True
        return super().eventFilter(obj, event)

    # ------------------------------------------------------------------
    # Rename logic
    # ------------------------------------------------------------------

    def _try_commit(self) -> None:
        """Validate the new name and perform the rename, or mark a conflict.

        Performs a case-sensitive (Linux) or case-insensitive (Windows)
        collision check.  On collision, beeps, sets RENAME_CONFLICT on the
        conflicting node, and leaves the editor open.  On success, stores the
        pending rename and closes the editor cleanly.
        """
        editor = self._current_editor
        if editor is None or self._rename_index is None:
            return

        new_name = editor.text().strip()
        index = QModelIndex(self._rename_index)
        if not index.isValid():
            return

        node: FileNode = index.internalPointer()
        old_path: Path = node.path
        parent_dir: Path = old_path.parent

        if not new_name or new_name == old_path.name:
            # Empty name or no change — treat as cancel
            self._exit_rename()
            self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.RevertModelData)
            return

        # Collision check (case-sensitive on Linux, case-insensitive on Windows)
        try:
            existing = set(e.name for e in parent_dir.iterdir())
        except OSError:
            existing = set()

        on_windows = sys.platform == "win32"
        if on_windows:
            collision_names = {n.lower() for n in existing if n.lower() != old_path.name.lower()}
            collision = new_name.lower() in collision_names
        else:
            collision_names = existing - {old_path.name}
            collision = new_name in collision_names

        if collision:
            try:
                QApplication.beep()
            except Exception:
                pass
            # Mark the conflicting node with RENAME_CONFLICT (slot 4 warning icon)
            self._mark_conflict(parent_dir, new_name, on_windows)
            # Write a plain-language message to the status bar
            self._view.status_message.emit(
                f"A file named \u2018{new_name}\u2019 already exists in this folder."
            )
            # Do NOT close the editor; leave user in rename mode
            return

        # No collision: perform the rename via business logic layer
        new_path = parent_dir / new_name
        rename_result = rename_item(old_path, new_path)
        if not rename_result.success:
            _error_dialog(
                self._view,
                "Rename Failed",
                rename_result.error_msg or f"Could not rename \u2018{old_path.name}\u2019.",
            )
            self._exit_rename()
            self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.RevertModelData)
            return

        # Store the rename so destroyEditor can apply model/cache updates after
        # the editor is fully torn down.  Doing it here (while the editor is
        # still alive) causes refresh_dir to clear the selection mid-teardown,
        # which lets the Enter key reach the now-visible EmptyStateWidget button.
        self._pending_rename = (old_path, new_path)
        self._exit_rename()
        self.closeEditor.emit(editor, QAbstractItemDelegate.EndEditHint.NoHint)

    def _mark_conflict(self, parent_dir: Path, new_name: str, case_insensitive: bool) -> None:
        """Find the conflicting node and set its slot-4 to RENAME_CONFLICT."""
        model = self._view.model()
        if not isinstance(model, FileTreeModel):
            return
        conflict_path = parent_dir / new_name
        # Try to find an existing node for the conflict (case-insensitive if needed)
        conflict_node: FileNode | None = None
        if case_insensitive:
            for path_str, node in model._path_to_node.items():
                if node.path.parent == parent_dir and node.name.lower() == new_name.lower():
                    conflict_node = node
                    break
        else:
            conflict_node = model._path_to_node.get(str(conflict_path))
        if conflict_node is not None and self._conflict_node is None:
            self._conflict_node = conflict_node
            model.set_rename_conflict(conflict_node)

    def _exit_rename(self) -> None:
        """Clean up rename-mode state: clear conflict icon, status bar."""
        self._editing = False
        self._current_editor = None
        self._rename_index = None

        # Clear conflict icon and restore prior slot-4 state
        if self._conflict_node is not None:
            model = self._view.model()
            if isinstance(model, FileTreeModel):
                model.clear_rename_conflict(self._conflict_node)
            self._conflict_node = None

        self._view.status_clear.emit()


# ---------------------------------------------------------------------------
# Per-file conflict dialog (merge)
# ---------------------------------------------------------------------------

class _ConflictDialog(QDialog):
    """Ask overwrite / skip for a single filename collision during a merge."""

    def __init__(self, filename: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("File Conflict")
        self.setModal(True)
        self._action: ConflictAction = "skip"
        self._apply_to_all: bool = False

        layout = QVBoxLayout(self)
        label = QLabel(
            f"A file named \u2018{filename}\u2019 already exists at the destination.\n"
            "What would you like to do?"
        )
        label.setWordWrap(True)
        layout.addWidget(label)

        buttons = QDialogButtonBox()
        overwrite_btn = buttons.addButton("Overwrite", QDialogButtonBox.ButtonRole.AcceptRole)
        skip_btn = buttons.addButton("Skip", QDialogButtonBox.ButtonRole.RejectRole)
        overwrite_all_btn = buttons.addButton(
            "Overwrite All", QDialogButtonBox.ButtonRole.AcceptRole
        )
        skip_all_btn = buttons.addButton("Skip All", QDialogButtonBox.ButtonRole.RejectRole)
        layout.addWidget(buttons)

        overwrite_btn.clicked.connect(self._on_overwrite)
        skip_btn.clicked.connect(self._on_skip)
        overwrite_all_btn.clicked.connect(self._on_overwrite_all)
        skip_all_btn.clicked.connect(self._on_skip_all)

    def _on_overwrite(self) -> None:
        self._action = "overwrite"
        self._apply_to_all = False
        self.accept()

    def _on_skip(self) -> None:
        self._action = "skip"
        self._apply_to_all = False
        self.reject()

    def _on_overwrite_all(self) -> None:
        self._action = "overwrite"
        self._apply_to_all = True
        self.accept()

    def _on_skip_all(self) -> None:
        self._action = "skip"
        self._apply_to_all = True
        self.reject()

    def result_action(self) -> tuple[ConflictAction, bool]:
        """Return the user's choice as ``(action, apply_to_all)``.

        Returns:
            A tuple of the chosen ``ConflictAction`` (``'overwrite'`` or
            ``'skip'``) and a bool indicating whether it should apply to all
            remaining conflicts in the current merge.
        """
        return self._action, self._apply_to_all


# ---------------------------------------------------------------------------
# Plain helper dialogs
# ---------------------------------------------------------------------------

def _error_dialog(parent: QWidget, title: str, message: str) -> None:
    """Show a modal error dialog with an OK button.

    Args:
        parent: Parent widget for the dialog.
        title: Window title string.
        message: Human-readable error description shown in the dialog body.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setSizeGripEnabled(True)
    dlg.setMinimumWidth(420)
    layout = QVBoxLayout(dlg)
    layout.setSpacing(12)
    label = QLabel(message)
    label.setWordWrap(True)
    layout.addWidget(label)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok)
    buttons.accepted.connect(dlg.accept)
    layout.addWidget(buttons)
    dlg.exec()


def _confirm_dialog(parent: QWidget, title: str, message: str) -> bool:
    """Show a modal Yes/No confirmation dialog.

    Args:
        parent: Parent widget for the dialog.
        title: Window title string.
        message: Question or warning text shown in the dialog body.

    Returns:
        ``True`` if the user clicked Yes, ``False`` otherwise.
    """
    dlg = QDialog(parent)
    dlg.setWindowTitle(title)
    dlg.setSizeGripEnabled(True)
    dlg.setMinimumWidth(420)
    layout = QVBoxLayout(dlg)
    layout.setSpacing(12)
    label = QLabel(message)
    label.setWordWrap(True)
    layout.addWidget(label)
    buttons = QDialogButtonBox(
        QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No
    )
    buttons.accepted.connect(dlg.accept)
    buttons.rejected.connect(dlg.reject)
    layout.addWidget(buttons)
    return dlg.exec() == QDialog.DialogCode.Accepted


# ---------------------------------------------------------------------------
# FileTreeView
# ---------------------------------------------------------------------------

class FileTreeView(QTreeView):
    """QTreeView with full Stage 6 file operations."""

    file_selected = pyqtSignal(Path)
    folder_selected = pyqtSignal(Path)
    status_message = pyqtSignal(str)  # request to show a status bar message
    status_clear = pyqtSignal()       # request to clear the status bar

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setHeaderHidden(True)
        self.setUniformRowHeights(True)
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_context_menu)

        # Rename delegate (replaces the plain NodeIndicatorDelegate)
        self._rename_delegate = RenameDelegate(self, self)
        self.setItemDelegate(self._rename_delegate)

        # Hash cache reference (set by MainWindow after construction)
        self._cache = None

        # Drag-and-drop (internal only; cross-app DnD not supported).
        # We implement drag initiation manually in mouseMoveEvent so that
        # the drag starts reliably regardless of Qt's internal state machine.
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self._drag_start_pos: "QPoint | None" = None
        self._drag_source_path: Path | None = None
        # Set during an active drag so dragMoveEvent can update the overlay.
        self._drag_ghost_source_path: Path | None = None
        self._drag_ghost_height: int = 0

        # Movement mode state
        self._movement_mode: bool = False
        self._move_source_persistent: QPersistentModelIndex | None = None
        self._move_source_was_expanded: bool = False
        self._move_ghost_target: QPersistentModelIndex | None = None
        self._move_ghost_height: int = 0

        # Ghost overlay
        self._ghost_overlay = _GhostOverlay(self.viewport())
        self._ghost_overlay.resize(self.viewport().size())

    # ------------------------------------------------------------------
    # Cache injection
    # ------------------------------------------------------------------

    def set_cache(self, cache) -> None:
        """Inject the hash cache reference used for path updates after file operations.

        Args:
            cache: The application's shared ``HashCache`` instance.
        """
        self._cache = cache

    # ------------------------------------------------------------------
    # Viewport resize — keep ghost overlay sized correctly
    # ------------------------------------------------------------------

    def resizeEvent(self, event) -> None:
        """Keep the ghost overlay sized to the viewport on resize."""
        super().resizeEvent(event)
        self._ghost_overlay.resize(self.viewport().size())

    # ------------------------------------------------------------------
    # Keyboard navigation
    # ------------------------------------------------------------------

    def keyPressEvent(self, event: QKeyEvent) -> None:
        """Route keyboard events to rename, movement, delete, or navigation handlers.

        Args:
            event: The key event to process.
        """
        key = event.key()

        if self._movement_mode:
            self._handle_movement_key(key, event)
            return

        index = self.currentIndex()

        if key == Qt.Key.Key_F2:
            if not self._rename_delegate.is_editing and index.isValid():
                self.edit(index)
            event.accept()
            return

        if key in (Qt.Key.Key_M, Qt.Key.Key_F6):
            if not self._rename_delegate.is_editing and index.isValid():
                self._enter_movement_mode(index)
            event.accept()
            return

        if key == Qt.Key.Key_Delete:
            if not self._rename_delegate.is_editing and index.isValid():
                self._trigger_delete(index)
            event.accept()
            return

        if key in (Qt.Key.Key_Left, Qt.Key.Key_Backspace):
            self._handle_left(index)
            event.accept()
            return

        if key == Qt.Key.Key_Right:
            self._handle_right(index)
            event.accept()
            return

        if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._handle_enter(index)
            event.accept()
            return

        super().keyPressEvent(event)

    def _handle_movement_key(self, key: int, event: QKeyEvent) -> None:
        """Process keys while in keyboard movement mode.

        Arrow keys move the ghost; Enter commits the move; Escape cancels.

        Args:
            key: The Qt key code.
            event: The full key event (used for ``event.accept()`` calls).
        """
        if key == Qt.Key.Key_Up:
            self._move_ghost_step(-1)
            event.accept()
        elif key == Qt.Key.Key_Down:
            self._move_ghost_step(1)
            event.accept()
        elif key == Qt.Key.Key_Left:
            self._move_ghost_left()
            event.accept()
        elif key == Qt.Key.Key_Right:
            self._move_ghost_right()
            event.accept()
        elif key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            self._commit_movement()
            event.accept()
        elif key == Qt.Key.Key_Escape:
            self._cancel_movement()
            event.accept()
        elif key == Qt.Key.Key_F2:
            event.accept()  # no-op during movement mode
        else:
            # Allow other keys (e.g. expand via Space) to pass through.
            super().keyPressEvent(event)

    def _handle_left(self, index: QModelIndex) -> None:
        """Collapse the current directory, or move selection to its parent.

        Args:
            index: The currently selected model index.
        """
        if not index.isValid():
            return
        node: FileNode = index.internalPointer()
        if node.is_dir and self.isExpanded(index):
            self.collapse(index)
        else:
            parent = index.parent()
            if parent.isValid():
                self.setCurrentIndex(parent)

    def _handle_right(self, index: QModelIndex) -> None:
        """Expand the current directory, or move selection to its first child.

        Beeps for symlinks and non-directory files.

        Args:
            index: The currently selected model index.
        """
        if not index.isValid():
            return
        node: FileNode = index.internalPointer()
        if node.is_symlink:
            self._beep()
            return
        if node.is_dir:
            if not self.isExpanded(index):
                self.expand(index)
            else:
                first_child = self.model().index(0, 0, index)
                if first_child.isValid():
                    self.setCurrentIndex(first_child)
                else:
                    self._beep()
        else:
            self._beep()

    def _handle_enter(self, index: QModelIndex) -> None:
        """Toggle directory expansion, or emit ``file_selected`` for files.

        Args:
            index: The currently selected model index.
        """
        if not index.isValid():
            return
        node: FileNode = index.internalPointer()
        if node.is_dir:
            if self.isExpanded(index):
                self.collapse(index)
            else:
                self.expand(index)
        else:
            self.file_selected.emit(node.path)

    @staticmethod
    def _beep() -> None:
        """Emit a system beep; silently ignored if the platform does not support it."""
        try:
            QApplication.beep()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # Context menu
    # ------------------------------------------------------------------

    def _show_context_menu(self, position) -> None:
        """Build and show the right-click context menu for the item at *position*.

        Args:
            position: Cursor position in viewport coordinates.
        """
        index = self.indexAt(position)
        if not index.isValid():
            return

        menu = QMenu(self)
        rename_action = menu.addAction("Rename")
        rename_action.setEnabled(not self._movement_mode and not self._rename_delegate.is_editing)
        rename_action.triggered.connect(lambda: self.edit(index))

        move_action = menu.addAction("Move\u2026")
        move_action.setEnabled(not self._movement_mode and not self._rename_delegate.is_editing)
        move_action.triggered.connect(lambda: self._trigger_move_dialog(index))

        menu.addSeparator()

        delete_action = menu.addAction("Delete")
        delete_action.setEnabled(not self._movement_mode and not self._rename_delegate.is_editing)
        delete_action.triggered.connect(lambda: self._trigger_delete(index))

        menu.exec(self.viewport().mapToGlobal(position))

    # ------------------------------------------------------------------
    # Deletion
    # ------------------------------------------------------------------

    def _trigger_delete(self, index: QModelIndex) -> None:
        """Confirm with the user and delete the item at *index* via ``delete_item``.

        Args:
            index: The model index of the item to delete.
        """
        if not index.isValid():
            return
        node: FileNode = index.internalPointer()
        name = node.name
        if not _confirm_dialog(
            self,
            "Confirm Delete",
            f"Send \u2018{name}\u2019 to the trash?\n\nThis action can be undone from the trash.",
        ):
            return

        result = delete_item(node.path)
        if not result.success:
            _error_dialog(self, "Delete Failed", result.error_msg or "Could not delete the item.")
            return

        # Update model
        model = self.model()
        if isinstance(model, FileTreeModel):
            model.notify_path_removed(str(node.path))
            expanded = self._collect_all_expanded()
            model.refresh_dir(node.path.parent)
            self._restore_all_expanded(expanded)

        if self._cache is not None:
            # Remove from cache: update_path to nowhere is not meaningful;
            # the cache row will simply become stale — acceptable for Stage 6.
            pass

    # ------------------------------------------------------------------
    # Move dialog
    # ------------------------------------------------------------------

    def _trigger_move_dialog(self, index: QModelIndex) -> None:
        """Open a directory picker and move the item at *index* to the chosen destination.

        Args:
            index: The model index of the item to move.
        """
        if not index.isValid():
            return
        node: FileNode = index.internalPointer()
        model = self.model()
        root_path: Path | None = model.root_path() if isinstance(model, FileTreeModel) else None

        start_dir = str(root_path) if root_path else str(node.path.parent)
        dst_str = QFileDialog.getExistingDirectory(
            self, f"Move \u2018{node.name}\u2019 to\u2026", start_dir
        )
        if not dst_str:
            return

        dst_parent = Path(dst_str)

        # Enforce: destination must be inside the root
        if root_path is not None:
            try:
                dst_parent.relative_to(root_path)
            except ValueError:
                _error_dialog(
                    self,
                    "Invalid Destination",
                    "The destination must be inside the current root folder.",
                )
                return

        self._do_move(node.path, dst_parent)

    # ------------------------------------------------------------------
    # Shared move execution (called by keyboard mode, DnD, and dialog)
    # ------------------------------------------------------------------

    def _do_move(self, src: Path, dst_parent: Path) -> None:
        """Execute ``move_item`` for *src* → *dst_parent* and handle any errors.

        Args:
            src: The source item path.
            dst_parent: The destination directory path.
        """
        result = move_item(
            src,
            dst_parent,
            confirm_cross_fs=self._confirm_cross_fs,
            confirm_merge=self._confirm_merge,
            resolve_conflict=self._resolve_conflict,
            cache_update=self._cache_update,
            tree_refresh=self._tree_refresh,
        )
        if not result.success and result.error_msg:
            _error_dialog(self, "Move Failed", result.error_msg)
        if result.failures:
            self._report_move_failures(result.failures)

    def _confirm_cross_fs(self, size_str: str) -> bool:
        """Show a modal warning about a cross-filesystem copy-then-delete operation.

        Args:
            size_str: Human-readable size string for the data to be copied.

        Returns:
            ``True`` if the user confirmed, ``False`` to cancel.
        """
        return _confirm_dialog(
            self,
            "Cross-Filesystem Move",
            f"The source and destination are on different filesystems.\n\n"
            f"This will copy all data (up to {size_str}) and then delete the originals "
            f"— it is not a simple relink.\n\nContinue?",
        )

    def _confirm_merge(self, src_name: str, dst_path_str: str) -> bool:
        """Show a modal warning that moving *src_name* will merge with an existing directory.

        Args:
            src_name: Name of the source directory being moved.
            dst_path_str: Absolute path string of the existing destination directory.

        Returns:
            ``True`` if the user confirmed the merge, ``False`` to cancel.
        """
        return _confirm_dialog(
            self,
            "Directory Merge",
            f"A folder named \u2018{src_name}\u2019 already exists at the destination.\n\n"
            f"Moving will merge the two folders. Any conflicting files will be handled "
            f"individually.\n\nContinue?",
        )

    def _resolve_conflict(self, filename: str) -> tuple[ConflictAction, bool]:
        """Show the per-file conflict dialog and return the user's choice.

        Args:
            filename: Name of the file that already exists at the destination.

        Returns:
            A ``(ConflictAction, apply_to_all)`` tuple.
        """
        dlg = _ConflictDialog(filename, self)
        dlg.exec()
        return dlg.result_action()

    def _cache_update(self, old_path: str, new_path: str) -> None:
        """Update the hash cache entry when a file is renamed or moved.

        Args:
            old_path: Absolute path string before the operation.
            new_path: Absolute path string after the operation.
        """
        if self._cache is not None:
            self._cache.update_path(old_path, new_path)

    def _collect_all_expanded(self) -> list[str]:
        """Return the paths of every currently-expanded node in the tree."""
        result: list[str] = []
        self._collect_expanded_recursive(QModelIndex(), result)
        return result

    def _collect_expanded_recursive(self, parent: QModelIndex, result: list[str]) -> None:
        """Recursively append expanded path strings under *parent* to *result*.

        Args:
            parent: The model index to iterate from; invalid means the root.
            result: Accumulator list that receives expanded path strings.
        """
        model = self.model()
        if model is None:
            return
        for row in range(model.rowCount(parent)):
            idx = model.index(row, 0, parent)
            if self.isExpanded(idx):
                node = idx.internalPointer()
                result.append(str(node.path))
                self._collect_expanded_recursive(idx, result)

    def _restore_all_expanded(self, paths: list[str]) -> None:
        """Re-expand nodes by path after rows have been removed and re-inserted."""
        model = self.model()
        if not isinstance(model, FileTreeModel):
            return
        # Restore shallowest paths first so parents are expanded before children.
        for path_str in sorted(paths, key=lambda p: len(Path(p).parts)):
            idx = model.index_for_path(Path(path_str))
            if idx.isValid():
                self.expand(idx)

    def _tree_refresh(self, old_path: Path, new_path: Path) -> None:
        """Update hash tracking and refresh affected directories after a move.

        Args:
            old_path: Source path before the move.
            new_path: Destination path after the move.
        """
        model = self.model()
        if not isinstance(model, FileTreeModel):
            return
        model.notify_path_changed(str(old_path), str(new_path))
        # Snapshot expansion state before any rows are removed; refresh_dir
        # removes and re-inserts rows, invalidating Qt's tracked expanded set.
        expanded = self._collect_all_expanded()
        model.refresh_dir(old_path.parent)
        if new_path.parent != old_path.parent:
            model.refresh_dir(new_path.parent)
        if new_path.is_dir():
            model.refresh_dir(new_path)
        self._restore_all_expanded(expanded)

    def _report_move_failures(self, failures: list[tuple[str, str]]) -> None:
        """Show an error dialog summarising per-file failures from a directory merge.

        Args:
            failures: List of ``(path, reason)`` pairs from ``MoveResult.failures``.
        """
        lines = "\n".join(f"  \u2022 {Path(p).name}: {msg}" for p, msg in failures)
        _error_dialog(
            self,
            "Move Completed with Errors",
            f"The following files could not be moved:\n\n{lines}",
        )

    # ------------------------------------------------------------------
    # Drag-and-drop — manual initiation via mousePressEvent / mouseMoveEvent
    # ------------------------------------------------------------------
    # Qt's built-in drag-start state machine (DraggingState) is unreliable
    # in this configuration (custom delegate + ItemIsEditable + custom model).
    # We track the press position ourselves and create the QDrag explicitly
    # once the cursor moves beyond startDragDistance.

    def mousePressEvent(self, event) -> None:
        """Record the drag start position and source path on a left-button press."""
        if event.button() == Qt.MouseButton.LeftButton and not self._movement_mode:
            idx = self.indexAt(event.position().toPoint())
            if idx.isValid():
                node: FileNode = idx.internalPointer()
                self._drag_start_pos = event.position().toPoint()
                self._drag_source_path = node.path
            else:
                self._drag_start_pos = None
                self._drag_source_path = None
        super().mousePressEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        """Clear drag state on mouse release."""
        self._drag_start_pos = None
        self._drag_source_path = None
        super().mouseReleaseEvent(event)

    def mouseMoveEvent(self, event) -> None:
        """Initiate a drag once the cursor moves beyond ``startDragDistance``."""
        if (
            event.buttons() & Qt.MouseButton.LeftButton
            and self._drag_start_pos is not None
            and self._drag_source_path is not None
            and not self._movement_mode
            and not self._rename_delegate.is_editing
        ):
            dist = (event.position().toPoint() - self._drag_start_pos).manhattanLength()
            if dist >= QApplication.startDragDistance():
                self._initiate_drag(self._drag_source_path)
                self._drag_start_pos = None
                self._drag_source_path = None
                return
        super().mouseMoveEvent(event)

    def _initiate_drag(self, src_path: Path) -> None:
        """Create and execute a ``QDrag`` for *src_path*, showing the ghost overlay.

        The ghost is shown before ``drag.exec()`` blocks so it is visible during
        the entire drag.  It is always cleared in the ``finally`` block.

        Args:
            src_path: Absolute ``Path`` of the item being dragged.
        """
        # Prepare the ghost overlay before exec() blocks the call.
        model = self.model()
        if isinstance(model, FileTreeModel):
            src_idx = model.index_for_path(src_path)
            if src_idx.isValid():
                self._drag_ghost_height = self._compute_ghost_height(src_idx)
                row_rect = self.visualRect(src_idx)
                ghost_rect = QRect(
                    row_rect.left(), row_rect.top(),
                    row_rect.width(), self._drag_ghost_height,
                )
                self._ghost_overlay.show_ghost(ghost_rect, False)

        self._drag_ghost_source_path = src_path

        mime = QMimeData()
        mime.setData(_DRAG_MIME_TYPE, str(src_path).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        try:
            drag.exec(Qt.DropAction.MoveAction)
        finally:
            self._ghost_overlay.clear_ghost()
            self._drag_ghost_source_path = None
            self._drag_ghost_height = 0

    def dragEnterEvent(self, event) -> None:
        """Accept internal drags carrying the echofinder path MIME type."""
        if event.source() is self and event.mimeData().hasFormat(_DRAG_MIME_TYPE):
            event.acceptProposedAction()
        else:
            event.ignore()

    def dragMoveEvent(self, event) -> None:
        """Keep the ghost overlay updated as the drag cursor moves over the tree."""
        if event.source() is self and event.mimeData().hasFormat(_DRAG_MIME_TYPE):
            event.acceptProposedAction()
            if self._drag_ghost_source_path is not None:
                self._update_drag_ghost(event.position().toPoint())
        else:
            self._ghost_overlay.clear_ghost()
            event.ignore()

    def _update_drag_ghost(self, viewport_pos: QPoint) -> None:
        """Reposition the ghost overlay to follow the cursor during a drag."""
        target_idx = self.indexAt(viewport_pos)
        if not target_idx.isValid():
            self._ghost_overlay.clear_ghost()
            return
        target_node: FileNode = target_idx.internalPointer()
        row_rect = self.visualRect(target_idx)
        if target_node.is_dir and self.isExpanded(target_idx):
            ghost_top = row_rect.bottom()
        else:
            ghost_top = row_rect.top()
        ghost_rect = QRect(
            row_rect.left(), ghost_top,
            row_rect.width(), self._drag_ghost_height,
        )
        collision = check_name_collision(
            target_node.path if (target_node.is_dir and self.isExpanded(target_idx))
            else target_node.path.parent,
            self._drag_ghost_source_path.name,
        )
        self._ghost_overlay.show_ghost(ghost_rect, collision)

    def dropEvent(self, event) -> None:
        """Execute the move for an internal drop operation."""
        self._ghost_overlay.clear_ghost()
        if event.source() is not self:
            event.ignore()
            return
        mime = event.mimeData()
        if not mime.hasFormat(_DRAG_MIME_TYPE):
            event.ignore()
            return

        src_path = Path(mime.data(_DRAG_MIME_TYPE).data().decode("utf-8"))

        pos = event.position().toPoint()
        dst_index = self.indexAt(pos)

        if not dst_index.isValid():
            model = self.model()
            dst_parent: Path | None = (
                model.root_path() if isinstance(model, FileTreeModel) else None
            )
        else:
            dst_node: FileNode = dst_index.internalPointer()
            dst_parent = dst_node.path if dst_node.is_dir else dst_node.path.parent

        event.accept()

        if dst_parent is None or dst_parent == src_path.parent:
            return

        self._do_move(src_path, dst_parent)

    # ------------------------------------------------------------------
    # Keyboard movement mode
    # ------------------------------------------------------------------

    def _enter_movement_mode(self, index: QModelIndex) -> None:
        """Enter keyboard movement mode for the item at *index*.

        Hides the source row, initialises the ghost at the current position,
        and sets ``_movement_mode`` so that subsequent key events are routed
        to ``_handle_movement_key``.

        Args:
            index: The model index of the item to move.
        """
        if not index.isValid():
            return
        node: FileNode = index.internalPointer()
        self._movement_mode = True
        self._move_source_persistent = QPersistentModelIndex(index)
        self._move_source_was_expanded = node.is_dir and self.isExpanded(index)
        # Lock expand state for the source: prevent expand/collapse of the moving item.
        # Other directories remain interactive.
        self._move_ghost_target = QPersistentModelIndex(index)

        # Calculate ghost height (source group bounding rect)
        self._move_ghost_height = self._compute_ghost_height(index)

        # Conceal the source row in the delegate
        self._rename_delegate.set_hidden_source(index)
        self.viewport().update()

        # Show ghost at current position
        self._update_ghost()

    def _compute_ghost_height(self, index: QModelIndex) -> int:
        """Height in pixels of the source item's visible group."""
        row_rect = self.visualRect(index)
        if row_rect.isNull():
            return 22  # fallback row height
        node: FileNode = index.internalPointer()
        if not (node.is_dir and self.isExpanded(index)):
            return row_rect.height()

        # Expanded directory: measure from top of dir row to bottom of last visible descendant
        bottom = row_rect.bottom()
        last_row = self._last_visible_descendant(index)
        if last_row.isValid():
            last_rect = self.visualRect(last_row)
            if not last_rect.isNull():
                bottom = last_rect.bottom()
        return max(row_rect.height(), bottom - row_rect.top())

    def _last_visible_descendant(self, index: QModelIndex) -> QModelIndex:
        """Return the deepest last visible descendant of *index*."""
        model = self.model()
        last = QModelIndex()
        row_count = model.rowCount(index)
        if row_count == 0:
            return last
        last_child = model.index(row_count - 1, 0, index)
        if not last_child.isValid():
            return last
        last = last_child
        if self.isExpanded(last_child):
            deeper = self._last_visible_descendant(last_child)
            if deeper.isValid():
                last = deeper
        return last

    def _cancel_movement(self) -> None:
        """Exit movement mode without committing; restore the original selection."""
        self._movement_mode = False
        self._ghost_overlay.clear_ghost()
        self._rename_delegate.set_hidden_source(None)
        # Restore selection to the original item
        if self._move_source_persistent is not None:
            src_idx = QModelIndex(self._move_source_persistent)
            if src_idx.isValid():
                self.setCurrentIndex(src_idx)
        self._move_source_persistent = None
        self._move_ghost_target = None
        self.viewport().update()

    def _commit_movement(self) -> None:
        """Commit the ghost position as the move destination and execute the move."""
        if self._move_source_persistent is None or self._move_ghost_target is None:
            self._cancel_movement()
            return

        src_idx = QModelIndex(self._move_source_persistent)
        target_idx = QModelIndex(self._move_ghost_target)
        if not src_idx.isValid() or not target_idx.isValid():
            self._cancel_movement()
            return

        src_node: FileNode = src_idx.internalPointer()
        dst_parent = self._ghost_destination_parent()
        if dst_parent is None:
            self._cancel_movement()
            return

        # Exit movement mode first (restores tree display)
        self._movement_mode = False
        self._ghost_overlay.clear_ghost()
        self._rename_delegate.set_hidden_source(None)
        self._move_source_persistent = None
        self._move_ghost_target = None
        self.viewport().update()

        # Same location? no-op
        if dst_parent == src_node.path.parent:
            return

        self._do_move(src_node.path, dst_parent)

    def _move_ghost_step(self, direction: int) -> None:
        """Move ghost up (direction=-1) or down (+1)."""
        if self._move_ghost_target is None:
            return
        target = QModelIndex(self._move_ghost_target)
        if not target.isValid():
            return

        hidden = (
            QModelIndex(self._move_source_persistent)
            if self._move_source_persistent
            else QModelIndex()
        )

        # Iterate to skip the hidden source row
        current = target
        for _ in range(self.model().rowCount() + 100):
            if direction > 0:
                nxt = self.indexBelow(current)
            else:
                nxt = self.indexAbove(current)
            if not nxt.isValid():
                break
            if hidden.isValid() and nxt == hidden:
                current = nxt  # skip and keep searching
                continue
            self._move_ghost_target = QPersistentModelIndex(nxt)
            break

        self._update_ghost()

    def _move_ghost_left(self) -> None:
        """Move ghost to the parent of its current target."""
        if self._move_ghost_target is None:
            return
        target = QModelIndex(self._move_ghost_target)
        if not target.isValid():
            return
        parent = target.parent()
        if parent.isValid():
            self._move_ghost_target = QPersistentModelIndex(parent)
            self._update_ghost()

    def _move_ghost_right(self) -> None:
        """If current target is an expanded dir, move ghost inside it (first child)."""
        if self._move_ghost_target is None:
            return
        target = QModelIndex(self._move_ghost_target)
        if not target.isValid():
            return
        node: FileNode = target.internalPointer()
        if node.is_dir and self.isExpanded(target):
            # Ghost is now "positioned over" this expanded dir (as first child)
            # The destination parent IS this directory; we stay on the dir index.
            pass  # ghost target remains the dir; it IS the destination
        self._update_ghost()

    def _ghost_destination_parent(self) -> Path | None:
        """Compute the destination parent directory from the ghost position."""
        if self._move_ghost_target is None:
            return None
        target = QModelIndex(self._move_ghost_target)
        if not target.isValid():
            return None
        node: FileNode = target.internalPointer()
        # Ghost is "over" an expanded directory → move INTO that dir
        if node.is_dir and self.isExpanded(target):
            return node.path
        # Otherwise move INTO the target's parent dir
        parent = target.parent()
        if parent.isValid():
            parent_node: FileNode = parent.internalPointer()
            return parent_node.path
        # Ghost is at root level
        model = self.model()
        if isinstance(model, FileTreeModel):
            return model.root_path()
        return None

    def _check_ghost_collision(self) -> bool:
        """Return True when the ghost's current position would cause a directory merge."""
        if self._move_source_persistent is None or self._move_ghost_target is None:
            return False
        src_idx = QModelIndex(self._move_source_persistent)
        if not src_idx.isValid():
            return False
        src_node: FileNode = src_idx.internalPointer()
        if not src_node.is_dir:
            return False  # only dirs can cause merges
        dst_parent = self._ghost_destination_parent()
        if dst_parent is None:
            return False
        return check_name_collision(dst_parent, src_node.name)

    def _update_ghost(self) -> None:
        """Recalculate ghost rect from the current target and refresh the overlay."""
        if self._move_ghost_target is None:
            self._ghost_overlay.clear_ghost()
            return
        target = QModelIndex(self._move_ghost_target)
        if not target.isValid():
            self._ghost_overlay.clear_ghost()
            return

        row_rect = self.visualRect(target)
        if row_rect.isNull():
            self._ghost_overlay.clear_ghost()
            return

        node: FileNode = target.internalPointer()
        if node.is_dir and self.isExpanded(target):
            # Ghost appears as first child of this directory:
            # draw the ghost just below the directory row.
            ghost_top = row_rect.bottom()
            ghost_rect = QRect(
                row_rect.left(),
                ghost_top,
                row_rect.width(),
                self._move_ghost_height,
            )
        else:
            ghost_rect = QRect(
                row_rect.left(),
                row_rect.top(),
                row_rect.width(),
                self._move_ghost_height,
            )

        collision = self._check_ghost_collision()
        self._ghost_overlay.show_ghost(ghost_rect, collision)
