"""File tree model with four-slot indicator support.

Stage 3 additions over Stage 2:
- ``_path_to_node``: index of all currently loaded FileNode objects by path string.
  Populated incrementally as directories are expanded (fetchMore).
- ``_path_to_hash`` / ``_hash_to_paths``: duplicate-group tracking built from
  file_hashed signals emitted by HashingEngine.
- ``_active_path`` / ``_active_hash``: the currently selected file in the main
  pane; used to distinguish DUPLICATE_SPECIFIC from DUPLICATE_GENERAL.
- Custom item roles SLOT2_ROLE, SLOT3_ROLE, SLOT4_ROLE return QIcon | None for
  the four-slot indicator system rendered by NodeIndicatorDelegate.

The rename-conflict state (HashState.RENAME_CONFLICT) is fully modelled here and
its mutual exclusivity logic is enforced; Stage 6 activates it via
``set_rename_conflict`` / ``clear_rename_conflict``.
"""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QAbstractItemModel, QModelIndex, Qt, pyqtSlot
from PyQt6.QtGui import QIcon

from echofinder.models.file_node import (
    FileNode,
    FileType,
    HashState,
    OwnershipState,
    PermissionState,
)
from echofinder.models.scanner import scan_directory
from echofinder.services.file_type import FileTypeResolver
from echofinder.ui.icons import (
    icon_for_hash_state,
    icon_for_ownership,
    icon_for_permission,
    icon_for_type,
)
from echofinder.ui.node_delegate import SLOT2_ROLE, SLOT3_ROLE, SLOT4_ROLE


class FileTreeModel(QAbstractItemModel):
    """``QAbstractItemModel`` backing the file tree view with four-slot indicators.

    Nodes are loaded lazily via ``canFetchMore`` / ``fetchMore``.  Duplicate-group
    tracking is maintained in ``_path_to_hash`` / ``_hash_to_paths`` and updated
    each time a ``file_hashed`` signal arrives from the hashing engine.

    Attributes:
        _resolver: ``FileTypeResolver`` forwarded to ``scan_directory``.
        _root: Root ``FileNode``, or ``None`` before ``set_root`` is called.
        _path_to_node: Maps path strings to loaded ``FileNode`` objects.
        _path_to_hash: Maps path strings to their SHA-256 hash.
        _hash_to_paths: Maps SHA-256 hashes to the set of paths sharing them.
        _active_path: Path of the currently selected file (for DUPLICATE_SPECIFIC).
        _active_hash: Hash of the currently selected file.
    """

    def __init__(self, resolver: FileTypeResolver, parent=None) -> None:
        """Initialise the model with an empty root.

        Args:
            resolver: Shared ``FileTypeResolver`` instance.
            parent: Optional Qt parent object.
        """
        super().__init__(parent)
        self._resolver = resolver
        self._root: FileNode | None = None

        # Path → loaded FileNode (populated lazily as dirs are expanded)
        self._path_to_node: dict[str, FileNode] = {}

        # Duplicate-group tracking (updated by on_file_hashed)
        self._path_to_hash: dict[str, str] = {}        # path → sha256
        self._hash_to_paths: dict[str, set[str]] = {}  # sha256 → set of paths

        # Currently active selection (for DUPLICATE_SPECIFIC distinction)
        self._active_path: str | None = None
        self._active_hash: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_root(self, path: Path) -> None:
        """Reset the model and set *path* as the new root directory.

        Clears all per-root state (path index, hash tracking, active file)
        and eagerly loads the root's direct children so the tree is not empty
        immediately after the root is set.

        Args:
            path: Absolute ``Path`` to the directory that will become the
                tree root.
        """
        self.beginResetModel()
        self._root = FileNode(path=path, file_type=FileType.FOLDER)
        # Clear all per-root state
        self._path_to_node.clear()
        self._path_to_hash.clear()
        self._hash_to_paths.clear()
        self._active_path = None
        self._active_hash = None
        self.endResetModel()
        # Eagerly load the root's direct children — they are always visible
        if self.canFetchMore(QModelIndex()):
            self.fetchMore(QModelIndex())

    def root_path(self) -> Path | None:
        """Return the root directory ``Path``, or ``None`` if no root is set.

        Returns:
            The root ``Path``, or ``None``.
        """
        return self._root.path if self._root else None

    def index_for_path(self, path: Path) -> QModelIndex:
        """Return the model index for *path*, loading ancestor nodes as needed."""
        if self._root is None:
            return QModelIndex()
        try:
            rel_parts = path.relative_to(self._root.path).parts
        except ValueError:
            return QModelIndex()

        current_index = QModelIndex()
        current_node = self._root

        for part in rel_parts:
            if not current_node.children_loaded:
                self.fetchMore(current_index)
            if not current_node.children:
                return QModelIndex()
            found = False
            for child in current_node.children:
                if child.name == part:
                    current_index = self.createIndex(child.row, 0, child)
                    current_node = child
                    found = True
                    break
            if not found:
                return QModelIndex()

        return current_index

    def set_active_file(self, path: str | None) -> None:
        """Notify the model that the main-pane selection has changed.

        Drives the DUPLICATE_SPECIFIC ↔ DUPLICATE_GENERAL transition across all
        affected loaded nodes.
        """
        old_active_hash = self._active_hash
        old_active_path = self._active_path

        self._active_path = path
        self._active_hash = self._path_to_hash.get(path) if path else None

        # Revert all nodes that were DUPLICATE_SPECIFIC under the old selection.
        # Always revert (even if old_active_hash == new_active_hash) so that
        # switching between two duplicates of each other works correctly.
        if old_active_hash:
            for p in self._hash_to_paths.get(old_active_hash, set()):
                if p == old_active_path:
                    continue
                node = self._path_to_node.get(p)
                if node is not None and node.hash_state == HashState.DUPLICATE_SPECIFIC:
                    node.hash_state = HashState.DUPLICATE_GENERAL
                    self._emit_slot4_changed(node)

        # Promote all DUPLICATE_GENERAL nodes matching the new selection to SPECIFIC.
        if self._active_hash:
            for p in self._hash_to_paths.get(self._active_hash, set()):
                if p == path:
                    continue
                node = self._path_to_node.get(p)
                if node is not None and node.hash_state == HashState.DUPLICATE_GENERAL:
                    node.hash_state = HashState.DUPLICATE_SPECIFIC
                    self._emit_slot4_changed(node)

    # ------------------------------------------------------------------
    # Rename-conflict override (Stage 6 API — model complete, not yet wired)
    # ------------------------------------------------------------------

    def set_rename_conflict(self, node: FileNode) -> None:
        """Temporarily override slot 4 with RENAME_CONFLICT; save prior state."""
        if node.hash_state == HashState.RENAME_CONFLICT:
            return
        node.prior_hash_state = node.hash_state
        node.hash_state = HashState.RENAME_CONFLICT
        self._emit_slot4_changed(node)

    def clear_rename_conflict(self, node: FileNode) -> None:
        """Exit rename mode; restore the slot-4 state that was active before."""
        if node.hash_state != HashState.RENAME_CONFLICT:
            return
        node.hash_state = node.prior_hash_state
        self._emit_slot4_changed(node)

    # ------------------------------------------------------------------
    # Slot: receives file_hashed signal from HashingEngine (cross-thread)
    # ------------------------------------------------------------------

    @pyqtSlot(str, str, str, str)
    def on_file_hashed(self, path: str, hash_val: str, ft: str, _lang: str) -> None:
        """Update duplicate tracking and node slot-4 state when a hash arrives."""
        # Promote file_type from extension guess to MIME-detected type when magic
        # resolves to a concrete binary type (IMAGE/VIDEO/AUDIO/PDF).  UNKNOWN is
        # returned for text/* and application/* — those are already handled by the
        # extension fallback and should not overwrite a good extension-based guess.
        if ft:
            promoted = FileTypeResolver.mime_to_file_type(ft)
            if promoted != FileType.UNKNOWN:
                node = self._path_to_node.get(path)
                if node is not None and node.file_type != promoted:
                    node.file_type = promoted
                    idx = self._model_index_for_node(node)
                    if idx.isValid():
                        self.dataChanged.emit(idx, idx, [Qt.ItemDataRole.DecorationRole])

        # Record the hash
        self._path_to_hash[path] = hash_val
        group = self._hash_to_paths.setdefault(hash_val, set())
        prev_group_size = len(group)
        group.add(path)

        # Determine the correct state for this newly hashed file
        new_state = self._compute_hash_state(path, hash_val)

        # Update the node if it is loaded
        node = self._path_to_node.get(path)
        if node is not None and node.hash_state != HashState.RENAME_CONFLICT:
            if node.hash_state != new_state:
                node.hash_state = new_state
                self._emit_slot4_changed(node)

        # If this file created a new duplicate pair (group grew from 1 to 2),
        # every previously UNIQUE node with the same hash must become DUPLICATE_GENERAL
        # (or DUPLICATE_SPECIFIC if appropriate).
        if prev_group_size == 1:
            for other_path in group:
                if other_path == path:
                    continue
                other_node = self._path_to_node.get(other_path)
                if other_node is not None and other_node.hash_state not in (
                    HashState.RENAME_CONFLICT,
                    HashState.DUPLICATE_GENERAL,
                    HashState.DUPLICATE_SPECIFIC,
                ):
                    other_state = self._compute_hash_state(other_path, hash_val)
                    if other_node.hash_state != other_state:
                        other_node.hash_state = other_state
                        self._emit_slot4_changed(other_node)

    # ------------------------------------------------------------------
    # QAbstractItemModel interface
    # ------------------------------------------------------------------

    def index(self, row: int, col: int, parent: QModelIndex = QModelIndex()) -> QModelIndex:
        """Return the model index for the child at (*row*, *col*) under *parent*.

        Args:
            row: Row within *parent*.
            col: Column (always 0 in this single-column model).
            parent: Parent index; invalid index means the root.

        Returns:
            A valid ``QModelIndex`` wrapping the ``FileNode``, or an invalid
            index if the position is out of range.
        """
        if not self.hasIndex(row, col, parent):
            return QModelIndex()
        parent_node = self._root if not parent.isValid() else parent.internalPointer()
        if parent_node is None or parent_node.children is None:
            return QModelIndex()
        if row < len(parent_node.children):
            return self.createIndex(row, col, parent_node.children[row])
        return QModelIndex()

    def parent(self, index: QModelIndex) -> QModelIndex:  # type: ignore[override]
        """Return the parent index of *index*.

        Args:
            index: The child index whose parent is requested.

        Returns:
            The parent ``QModelIndex``, or an invalid index for top-level items.
        """
        if not index.isValid():
            return QModelIndex()
        node: FileNode = index.internalPointer()
        parent_node = node.parent
        if parent_node is None or parent_node is self._root:
            return QModelIndex()
        return self.createIndex(parent_node.row, 0, parent_node)

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """Return the number of loaded children under *parent*.

        Args:
            parent: Parent index; invalid means the root.

        Returns:
            Number of loaded child rows, or 0 if children are not yet fetched.
        """
        if parent.column() > 0:
            return 0
        node = self._root if not parent.isValid() else parent.internalPointer()
        if node is None or node.children is None:
            return 0
        return len(node.children)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        """Return 1; this is a single-column model.

        Args:
            parent: Unused.

        Returns:
            Always ``1``.
        """
        return 1

    def hasChildren(self, parent: QModelIndex = QModelIndex()) -> bool:
        """Return whether *parent* has (or may have) child rows.

        Returns ``True`` for unloaded directories so the expand arrow is shown
        before ``fetchMore`` is called.

        Args:
            parent: The index to test.

        Returns:
            ``True`` if the item is a directory with at least one child, or
            if it is an unloaded directory (assumed non-empty).
        """
        if not parent.isValid():
            return self._root is not None
        node: FileNode = parent.internalPointer()
        if node.is_symlink or not node.is_dir:
            return False
        if node.children is not None:
            return len(node.children) > 0
        return True  # Unloaded directory — assume it has children

    def canFetchMore(self, parent: QModelIndex) -> bool:
        """Return ``True`` if *parent* is a directory whose children are not yet loaded.

        Args:
            parent: The index to check.

        Returns:
            ``True`` when the node is an unloaded directory.
        """
        node = self._root if not parent.isValid() else (
            parent.internalPointer() if parent.isValid() else None
        )
        if node is None:
            return False
        if node.is_symlink or not node.is_dir:
            return False
        return not node.children_loaded

    def fetchMore(self, parent: QModelIndex) -> None:
        """Load and insert the children of *parent* into the model.

        Called by the view when the user expands a directory.  Children are
        scanned via ``scan_directory`` and registered in the path index.

        Args:
            parent: The directory index to expand.
        """
        node = self._root if not parent.isValid() else parent.internalPointer()
        if node is None or node.children_loaded:
            return

        children = self._scan_children(node)
        if children:
            self.beginInsertRows(parent, 0, len(children) - 1)
            node.children = children
            self.endInsertRows()
            for child in children:
                self._register_node(child)
        else:
            node.children = []
            # Notify view to re-check hasChildren (removes stale expand arrow)
            if parent.isValid():
                self.dataChanged.emit(parent, parent, [])

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        """Return data for *index* under *role*.

        Handles ``DisplayRole`` (filename), ``DecorationRole`` (slot-1 icon),
        and the three custom slot roles for ownership, permission, and hash
        state indicators.

        Args:
            index: The model index to query.
            role: Qt item data role constant.

        Returns:
            The requested data, or ``None`` if the role is not handled.
        """
        if not index.isValid():
            return None
        node: FileNode = index.internalPointer()

        if role == Qt.ItemDataRole.DisplayRole:
            return node.name

        if role == Qt.ItemDataRole.DecorationRole:
            return icon_for_type(node.file_type)

        # --- Slot 2: ownership (empty for symlinks per spec) ---
        if role == SLOT2_ROLE:
            if node.is_symlink:
                return None
            return icon_for_ownership(node.ownership)

        # --- Slot 3: permissions (empty for symlinks per spec) ---
        if role == SLOT3_ROLE:
            if node.is_symlink:
                return None
            return icon_for_permission(node.permission)

        # --- Slot 4: hashing/duplicate state (empty for symlinks per spec) ---
        if role == SLOT4_ROLE:
            if node.is_symlink:
                return None
            return icon_for_hash_state(node.hash_state)

        return None

    def flags(self, index: QModelIndex) -> Qt.ItemFlag:
        """Return item flags for *index*.

        All valid items are enabled, selectable, draggable, and editable
        (for rename).  Directories additionally accept drops.

        Args:
            index: The item to query.

        Returns:
            The combined ``Qt.ItemFlag`` value for the item.
        """
        if not index.isValid():
            # Allow drops on the viewport background (between all items)
            return Qt.ItemFlag.ItemIsDropEnabled
        base = (
            Qt.ItemFlag.ItemIsEnabled
            | Qt.ItemFlag.ItemIsSelectable
            | Qt.ItemFlag.ItemIsDragEnabled
            | Qt.ItemFlag.ItemIsEditable
        )
        node: FileNode = index.internalPointer()
        if node.is_dir:
            base |= Qt.ItemFlag.ItemIsDropEnabled
        return base

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _scan_children(self, node: FileNode) -> list[FileNode]:
        """Delegate to ``scanner.scan_directory`` using the current root path.

        Args:
            node: The directory node whose children should be scanned.

        Returns:
            Sorted list of child ``FileNode`` objects.
        """
        root_path = self._root.path if self._root else None
        return scan_directory(node, root_path, self._resolver)

    def _register_node(self, node: FileNode) -> None:
        """Add a newly loaded node to the path index and set its initial hash state.

        If a hash is already known for this path (e.g. it was hashed before the
        directory was expanded), apply the correct state immediately so the node
        never displays the wrong indicator.
        """
        path_str = str(node.path)
        self._path_to_node[path_str] = node

        # Only non-dir, non-symlink nodes participate in hashing
        if node.is_dir or node.is_symlink:
            return

        known_hash = self._path_to_hash.get(path_str)
        if known_hash is not None:
            # Hash already arrived before this node was loaded — apply correct state
            correct_state = self._compute_hash_state(path_str, known_hash)
            node.hash_state = correct_state
            # No dataChanged emit here — node just became visible with the right state

    def _compute_hash_state(self, path: str, hash_val: str) -> HashState:
        """Determine the correct HashState for a path given its hash."""
        group = self._hash_to_paths.get(hash_val, set())
        if len(group) <= 1:
            return HashState.UNIQUE
        if (
            self._active_hash == hash_val
            and path != self._active_path
        ):
            return HashState.DUPLICATE_SPECIFIC
        return HashState.DUPLICATE_GENERAL

    def _model_index_for_node(self, node: FileNode) -> QModelIndex:
        """Construct the QModelIndex for a loaded node.

        Uses node.row (position within its parent's children list), which is
        set at scan time and is stable for the lifetime of the node.
        """
        if node.parent is None or node is self._root:
            return QModelIndex()
        return self.createIndex(node.row, 0, node)

    # ------------------------------------------------------------------
    # Tree refresh after file operations (Stage 6)
    # ------------------------------------------------------------------

    def refresh_dir(self, dir_path: Path) -> None:
        """Reload the children of *dir_path*, discarding stale nodes.

        Called after deletion, rename, or move so the tree reflects the new
        filesystem state.  If *dir_path* is not currently loaded this is a
        no-op.
        """
        if self._root is None:
            return

        if dir_path == self._root.path:
            parent_index = QModelIndex()
            parent_node = self._root
        else:
            parent_index = self.index_for_path(dir_path)
            if not parent_index.isValid():
                return
            parent_node = parent_index.internalPointer()

        if not parent_node.children_loaded:
            return

        old_children = parent_node.children or []
        old_count = len(old_children)

        if old_count > 0:
            self.beginRemoveRows(parent_index, 0, old_count - 1)
            for child in old_children:
                self._unregister_node_recursive(child)
            parent_node.children = None
            self.endRemoveRows()
        else:
            parent_node.children = None

        if self.canFetchMore(parent_index):
            self.fetchMore(parent_index)
        else:
            # Notify the view to update the expand indicator
            if parent_index.isValid():
                self.dataChanged.emit(parent_index, parent_index, [])

    def notify_path_changed(self, old_path: str, new_path: str) -> None:
        """Update in-memory hash tracking after a rename or move."""
        # Update path→hash mapping
        hash_val = self._path_to_hash.pop(old_path, None)
        if hash_val is not None:
            self._path_to_hash[new_path] = hash_val
            group = self._hash_to_paths.get(hash_val)
            if group is not None:
                group.discard(old_path)
                group.add(new_path)
        # Update active-file tracking
        if self._active_path == old_path:
            self._active_path = new_path

    def notify_path_removed(self, path: str) -> None:
        """Remove a path from in-memory hash tracking after deletion."""
        hash_val = self._path_to_hash.pop(path, None)
        if hash_val is not None:
            group = self._hash_to_paths.get(hash_val)
            if group is not None:
                group.discard(path)
        if self._active_path == path:
            self._active_path = None
            self._active_hash = None

    # ------------------------------------------------------------------
    # Polling support (Stage 7)
    # ------------------------------------------------------------------

    def get_polling_snapshot(self) -> frozenset[str]:
        """Return the current set of loaded paths for the polling engine.

        Called from the main thread; the returned frozenset is passed to
        PollingEngine.update_known_paths() before each poll cycle.
        """
        return frozenset(self._path_to_node.keys())

    @pyqtSlot(list)
    def on_entries_removed(self, paths: list[str]) -> None:
        """Handle paths detected as removed by the polling engine.

        Updates hash tracking for each removed path, then refreshes the
        affected parent directories so the tree reflects the deletion.
        """
        parents: set[Path] = set()
        for path_str in paths:
            self.notify_path_removed(path_str)
            parents.add(Path(path_str).parent)
        for parent in parents:
            self.refresh_dir(parent)

    @pyqtSlot(list)
    def on_entries_changed(self, paths: list[str]) -> None:
        """Handle files detected as changed by the polling engine.

        Removes stale hash data and resets each affected node's slot-4
        indicator to NOT_HASHED (hourglass) so the user sees that re-hashing
        is in progress.  When re-hashing completes, on_file_hashed restores
        the correct state.

        If removing a changed path reduces a duplicate group to a single
        remaining member, that member is promoted back to UNIQUE.
        """
        for path_str in paths:
            # Remove from hash tracking so the stale duplicate grouping is
            # cleared before the new hash arrives.
            old_hash = self._path_to_hash.pop(path_str, None)
            if old_hash is not None:
                group = self._hash_to_paths.get(old_hash)
                if group is not None:
                    group.discard(path_str)
                    # A group of exactly one is no longer a duplicate group.
                    if len(group) == 1:
                        for other_path in group:
                            other_node = self._path_to_node.get(other_path)
                            if other_node is not None and other_node.hash_state in (
                                HashState.DUPLICATE_GENERAL,
                                HashState.DUPLICATE_SPECIFIC,
                            ):
                                other_node.hash_state = HashState.UNIQUE
                                self._emit_slot4_changed(other_node)
            # Clear active-hash tracking if the changed file was the active file.
            if self._active_path == path_str and old_hash is not None:
                self._active_hash = None

            # Reset slot-4 to hourglass on the loaded node (if any).
            node = self._path_to_node.get(path_str)
            if node is not None and node.hash_state != HashState.RENAME_CONFLICT:
                if not node.is_dir and not node.is_symlink:
                    node.hash_state = HashState.NOT_HASHED
                    self._emit_slot4_changed(node)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _unregister_node_recursive(self, node: FileNode) -> None:
        """Remove *node* and all its descendants from the path index."""
        self._path_to_node.pop(str(node.path), None)
        if node.children:
            for child in node.children:
                self._unregister_node_recursive(child)

    def _emit_slot4_changed(self, node: FileNode) -> None:
        """Emit dataChanged for slot 4 of *node* so the view repaints."""
        idx = self._model_index_for_node(node)
        if idx.isValid():
            self.dataChanged.emit(idx, idx, [SLOT4_ROLE])
