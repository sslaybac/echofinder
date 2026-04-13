"""Preview pane — QStackedWidget that orchestrates all content widgets.

Resolver precedence (Stage 4 spec §4, updated in Stage 8):
  1. Empty state  — no selection yet
  2. Symlink      — SYMLINK_INTERNAL / SYMLINK_EXTERNAL
  3. Folder       — FOLDER
  4. Image        — IMAGE  (MIME primary, extension fallback)
  5. Text / Code  — TEXT / CODE
  6. PDF          — PDF (Stage 8)
  7. Unsupported  — AUDIO, VIDEO, UNKNOWN (Stages 9-10 will add entries here)

All filesystem I/O is performed through echofinder.services.preview; individual
widgets receive pre-loaded data only.
"""
from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import QStackedWidget

from echofinder.models.file_node import FileNode, FileType, PermissionState
from echofinder.models.scanner import scan_directory
from echofinder.services.file_type import FileTypeResolver
from echofinder.services.preview import load_image_bytes, read_text_for_preview
from echofinder.ui.empty_state import EmptyStateWidget
from echofinder.ui.preview.folder_widget import FolderContentsWidget
from echofinder.ui.preview.image_widget import ImagePreviewWidget
from echofinder.ui.preview.pdf_widget import PDFPreviewWidget
from echofinder.ui.preview.symlink_widget import SymlinkWidget
from echofinder.ui.preview.text_widget import TextPreviewWidget
from echofinder.ui.preview.unreadable_widget import UnreadableWidget
from echofinder.ui.preview.unsupported_widget import UnsupportedWidget

# Widget slot indices — order must remain stable; new stages append before UNSUPPORTED
_IDX_EMPTY = 0
_IDX_IMAGE = 1
_IDX_TEXT = 2
_IDX_SYMLINK = 3
_IDX_FOLDER = 4
_IDX_UNSUPPORTED = 5
_IDX_UNREADABLE = 6
_IDX_PDF = 7


class PreviewPane(QStackedWidget):
    """Orchestrates all preview widgets via a QStackedWidget.

    Signals emitted to the main window:
      select_folder_requested — user clicked 'Select Root Folder' in the empty state
      navigate_to_path        — user clicked a jump link or folder item (emits Path)
      encoding_detected       — encoding name to show in the metadata panel (Stage 5)
    """

    select_folder_requested = pyqtSignal()
    navigate_to_path = pyqtSignal(object)   # Path
    encoding_detected = pyqtSignal(str)     # encoding name; consumed by Stage 5

    def __init__(self, parent=None) -> None:
        super().__init__(parent)

        self._resolver = FileTypeResolver()

        self._empty = EmptyStateWidget()
        self._image = ImagePreviewWidget()
        self._text = TextPreviewWidget()
        self._symlink = SymlinkWidget()
        self._folder = FolderContentsWidget()
        self._unsupported = UnsupportedWidget()
        self._unreadable = UnreadableWidget()
        self._pdf = PDFPreviewWidget()

        for widget in (
            self._empty,       # _IDX_EMPTY       = 0
            self._image,       # _IDX_IMAGE        = 1
            self._text,        # _IDX_TEXT         = 2
            self._symlink,     # _IDX_SYMLINK      = 3
            self._folder,      # _IDX_FOLDER       = 4
            self._unsupported, # _IDX_UNSUPPORTED  = 5
            self._unreadable,  # _IDX_UNREADABLE   = 6
            self._pdf,         # _IDX_PDF          = 7
        ):
            self.addWidget(widget)

        self.setCurrentIndex(_IDX_EMPTY)

        # Forward child signals to the main window
        self._empty.select_folder_requested.connect(self.select_folder_requested)
        self._symlink.navigate_to_path.connect(self.navigate_to_path)
        self._folder.navigate_to_path.connect(self.navigate_to_path)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def show_empty(self) -> None:
        """Show the empty state widget and release any held preview resources."""
        self._image.release()
        self._pdf.release()
        self.encoding_detected.emit("")
        self.setCurrentIndex(_IDX_EMPTY)

    def show_for_node(self, node: FileNode, root: Path | None) -> None:
        """Apply the resolver precedence order and activate the correct widget."""
        file_type = node.file_type

        # Release held preview resources unless we are loading the same type
        if file_type != FileType.IMAGE:
            self._image.release()
        if file_type != FileType.PDF:
            self._pdf.release()

        if file_type in (FileType.SYMLINK_INTERNAL, FileType.SYMLINK_EXTERNAL):
            self.encoding_detected.emit("")
            self._symlink.load(node.path, root)
            self.setCurrentIndex(_IDX_SYMLINK)

        elif file_type == FileType.FOLDER:
            self.encoding_detected.emit("")
            self._show_folder(node, root)

        elif file_type == FileType.IMAGE:
            self._show_image(node)

        elif file_type in (FileType.TEXT, FileType.CODE):
            self._show_text(node)

        elif file_type == FileType.PDF:
            self._show_pdf(node)

        else:
            # AUDIO, VIDEO, UNKNOWN → unsupported (Stages 9-10 will handle audio/video)
            self.encoding_detected.emit("")
            self._unsupported.show_for(node.path, file_type)
            self.setCurrentIndex(_IDX_UNSUPPORTED)

    # ------------------------------------------------------------------
    # Private loaders — each performs I/O via the service layer
    # ------------------------------------------------------------------

    def _show_folder(self, node: FileNode, root: Path | None) -> None:
        # Use already-loaded children if available (avoids a redundant scan);
        # otherwise scan via the same function used by the tree model so that
        # sort order matches what is shown in the tree (spec §5).
        if node.children is not None:
            children = node.children
        else:
            children = scan_directory(node, root, self._resolver)
        self._folder.load(node.path, children)
        self.setCurrentIndex(_IDX_FOLDER)

    def _show_image(self, node: FileNode) -> None:
        self.encoding_detected.emit("")
        if node.permission == PermissionState.NOT_READABLE:
            self._unreadable.show_for(node.path, is_permission_error=True)
            self.setCurrentIndex(_IDX_UNREADABLE)
            return
        try:
            data = load_image_bytes(node.path)
            self._image.load(node.path, data)
            self.setCurrentIndex(_IDX_IMAGE)
        except PermissionError:
            self._unreadable.show_for(node.path, is_permission_error=True)
            self.setCurrentIndex(_IDX_UNREADABLE)
        except OSError:
            self._unreadable.show_for(node.path, is_permission_error=False)
            self.setCurrentIndex(_IDX_UNREADABLE)

    def _show_text(self, node: FileNode) -> None:
        if node.permission == PermissionState.NOT_READABLE:
            self.encoding_detected.emit("")
            self._unreadable.show_for(node.path, is_permission_error=True)
            self.setCurrentIndex(_IDX_UNREADABLE)
            return
        try:
            text, encoding = read_text_for_preview(node.path)
            self._text.load(node.path, text, encoding)
            self.encoding_detected.emit(encoding)
            self.setCurrentIndex(_IDX_TEXT)
        except PermissionError:
            self.encoding_detected.emit("")
            self._unreadable.show_for(node.path, is_permission_error=True)
            self.setCurrentIndex(_IDX_UNREADABLE)
        except OSError:
            self.encoding_detected.emit("")
            self._unreadable.show_for(node.path, is_permission_error=False)
            self.setCurrentIndex(_IDX_UNREADABLE)

    def _show_pdf(self, node: FileNode) -> None:
        self.encoding_detected.emit("")
        if node.permission == PermissionState.NOT_READABLE:
            self._unreadable.show_for(node.path, is_permission_error=True)
            self.setCurrentIndex(_IDX_UNREADABLE)
            return
        self._pdf.load(node.path)
        self.setCurrentIndex(_IDX_PDF)
