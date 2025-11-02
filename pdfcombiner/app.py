from __future__ import annotations

import io
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, QSize, QUrl
from PyQt6.QtGui import QIcon, QPixmap, QImageReader
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QListWidget,
    QListView,
    QListWidgetItem,
    QFileDialog,
    QMessageBox,
    QStyle,
    QStatusBar,
)

from PIL import Image
from PIL.ImageQt import ImageQt

import pypdf
import pypdfium2 as pdfium


SUPPORTED_IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}
PDF_EXTS = {".pdf"}

THUMB_MAX_SIZE = QSize(180, 240)


@dataclass(frozen=True)
class PageData:
    kind: str  # 'pdf' or 'img'
    path: str
    page_index: Optional[int] = None  # required for kind 'pdf'

    @property
    def label(self) -> str:
        base = os.path.basename(self.path)
        if self.kind == "pdf" and self.page_index is not None:
            return f"{base} • p{self.page_index + 1}"
        return base


class ThumbListWidget(QListWidget):
    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setViewMode(QListWidget.ViewMode.IconMode)
        self.setIconSize(THUMB_MAX_SIZE)
        self.setFlow(QListView.Flow.LeftToRight)
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Snap)  # Snap to grid positions during drag
        self.setWrapping(True)
        self.setSpacing(12)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setDefaultDropAction(Qt.DropAction.MoveAction)
        # Make the grid spacing uniform
        self.setUniformItemSizes(True)
        self.setLayoutMode(QListWidget.LayoutMode.Batched)
        # Grid background and styling
        self.setStyleSheet("""
            QListWidget {
                background-color: #f5f5f5;
                border: 1px solid #ddd;
                padding: 8px;
            }
            QListWidget::item:selected {
                background-color: #e3f2fd;
                border: 2px solid #2196F3;
                border-radius: 4px;
            }
            QListWidget::item:hover {
                background-color: #f0f0f0;
                border-radius: 4px;
            }
        """)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            # External file drop
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            if any(is_supported_path(p) for p in paths):
                event.acceptProposedAction()
                return
        # Internal reordering
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            # External file drop in progress
            event.acceptProposedAction()
            return
        # Internal reordering
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            # External file drop - add files
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            parent = self.parent()
            if parent and hasattr(parent, 'add_files'):
                parent.add_files(paths)  # type: ignore[attr-defined]
            event.acceptProposedAction()
            return
        
        # Internal reordering - use Qt's default handler
        super().dropEvent(event)
        
        # Update order labels after move completes
        parent_window = self.parent()
        if parent_window and hasattr(parent_window, 'refresh_order_labels'):
            from PyQt6.QtCore import QTimer
            QTimer.singleShot(10, parent_window.refresh_order_labels)  # type: ignore[attr-defined]
    
    def keyPressEvent(self, event):
        # Delete key removes selected item
        if event.key() in (Qt.Key.Key_Delete, Qt.Key.Key_Backspace):
            current = self.currentItem()
            if current:
                row = self.row(current)
                self.takeItem(row)
                parent_window = self.parent()
                if parent_window and hasattr(parent_window, 'refresh_order_labels'):
                    parent_window.refresh_order_labels()  # type: ignore[attr-defined]
                    parent_window.statusBar().showMessage(f"Total pages: {self.count()}")  # type: ignore[attr-defined]
            event.accept()
        else:
            super().keyPressEvent(event)




class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("PDF Combiner")
        self.resize(1000, 700)
        self._pdf_readers_cache: dict[str, pypdf.PdfReader] = {}

        central = QWidget(self)
        vbox = QVBoxLayout(central)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(8)

        # Top bar with Add Files button
        top_bar = QHBoxLayout()
        self.add_btn = QPushButton("Add Files…")
        self.add_btn.setToolTip("Add images and PDFs")
        self.add_btn.clicked.connect(self.on_add_files)
        top_bar.addWidget(self.add_btn)
        top_bar.addStretch(1)
        vbox.addLayout(top_bar)

        # List of pages
        self.list = ThumbListWidget(self)
        vbox.addWidget(self.list, 1)

        # Update numbering on reorder/insert/remove
        self.list.model().rowsMoved.connect(lambda *args: self.refresh_order_labels())
        self.list.model().rowsRemoved.connect(lambda *args: self.refresh_order_labels())
        self.list.model().rowsInserted.connect(lambda *args: self.refresh_order_labels())

        # Bottom bar with Combine button
        bottom_bar = QHBoxLayout()
        bottom_bar.addStretch(1)
        self.combine_btn = QPushButton("Combine PDF")
        self.combine_btn.setEnabled(True)
        self.combine_btn.clicked.connect(self.on_combine)
        bottom_bar.addWidget(self.combine_btn)
        vbox.addLayout(bottom_bar)

        self.setCentralWidget(central)

        self.setStatusBar(QStatusBar(self))
        self.statusBar().showMessage("Add files to begin…")

    def on_add_files(self):
        filters = (
            "PDF and Images (*.pdf *.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;"
            "PDF Files (*.pdf);;"
            "Images (*.png *.jpg *.jpeg *.bmp *.tif *.tiff *.webp);;"
            "All Files (*.*)"
        )
        files, _ = QFileDialog.getOpenFileNames(self, "Select files", "", filters)
        if files:
            self.add_files(files)

    def add_files(self, paths: List[str]):
        for p in paths:
            if not os.path.isfile(p):
                continue
            ext = os.path.splitext(p)[1].lower()
            if ext in PDF_EXTS:
                self._add_pdf(p)
            elif ext in SUPPORTED_IMAGE_EXTS:
                self._add_image(p)
        self.refresh_order_labels()
        self.statusBar().showMessage(f"Total pages: {self.list.count()}")

    def _add_pdf(self, path: str):
        try:
            reader = pypdf.PdfReader(path)
        except Exception as e:
            QMessageBox.warning(self, "Error", f"Failed to read PDF: {os.path.basename(path)}\n{e}")
            return
        num_pages = len(reader.pages)
        for i in range(num_pages):
            pd = PageData(kind="pdf", path=path, page_index=i)
            thumb = get_thumbnail(pd)
            self._add_page_item(pd, thumb)

    def _add_image(self, path: str):
        pd = PageData(kind="img", path=path)
        thumb = get_thumbnail(pd)
        self._add_page_item(pd, thumb)

    def _add_page_item(self, page_data: PageData, thumb: QPixmap):
        item = QListWidgetItem()
        # Set thumbnail as icon
        item.setIcon(QIcon(thumb))
        # Set filename as text (order number will be prepended in refresh_order_labels)
        item.setText(page_data.label)
        item.setToolTip(f"{page_data.label}\nPress Delete to remove")
        # Size hint for the icon
        item.setSizeHint(QSize(THUMB_MAX_SIZE.width() + 20, THUMB_MAX_SIZE.height() + 50))
        item.setData(Qt.ItemDataRole.UserRole, page_data)
        item.setFlags(item.flags() | Qt.ItemFlag.ItemIsSelectable | Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsDragEnabled | Qt.ItemFlag.ItemIsDropEnabled)
        item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
        
        self.list.addItem(item)
        self.refresh_order_labels()

    def on_combine(self):
        if self.list.count() == 0:
            QMessageBox.information(self, "Nothing to do", "Add some pages first.")
            return
        default_name = os.path.expanduser(os.path.join("~", "combined.pdf"))
        out_path, _ = QFileDialog.getSaveFileName(self, "Save combined PDF", default_name, "PDF Files (*.pdf)")
        if not out_path:
            return
        if not out_path.lower().endswith(".pdf"):
            out_path += ".pdf"

        try:
            self._write_combined_pdf(out_path)
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to write PDF:\n{e}")
            return

        self.statusBar().showMessage(f"Saved: {out_path}")
        QMessageBox.information(self, "Done", f"Combined PDF saved to:\n{out_path}")

    def _write_combined_pdf(self, out_path: str):
        writer = pypdf.PdfWriter()

        for i in range(self.list.count()):
            item = self.list.item(i)
            page_data: PageData = item.data(Qt.ItemDataRole.UserRole)
            if page_data.kind == "pdf":
                reader = self._get_reader(page_data.path)
                writer.add_page(reader.pages[page_data.page_index])  # type: ignore[index]
            else:
                # Convert image page to a temporary in-memory one-page PDF and import
                img_pdf_bytes = image_to_pdf_bytes(page_data.path)
                tmp_reader = pypdf.PdfReader(io.BytesIO(img_pdf_bytes))
                writer.add_page(tmp_reader.pages[0])

        with open(out_path, "wb") as f:
            writer.write(f)

    def refresh_order_labels(self):
        for i in range(self.list.count()):
            item = self.list.item(i)
            page_data = item.data(Qt.ItemDataRole.UserRole)
            if page_data:
                # Update text with order number
                item.setText(f"{i + 1}. {page_data.label}")
    

    @lru_cache(maxsize=32)
    def _get_reader(self, path: str) -> pypdf.PdfReader:
        return pypdf.PdfReader(path)


def is_supported_path(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in SUPPORTED_IMAGE_EXTS or ext in PDF_EXTS


@lru_cache(maxsize=256)
def get_thumbnail(page_data: PageData) -> QPixmap:
    if page_data.kind == "img":
        # Prefer QImageReader with auto orientation and color profile handling.
        try:
            reader = QImageReader(page_data.path)
            reader.setAutoTransform(True)
            qimg = reader.read()
            if not qimg.isNull():
                pm = QPixmap.fromImage(qimg)
                return pm.scaled(THUMB_MAX_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        except Exception:
            pass
        # Fallback to QPixmap loader
        pm = QPixmap(page_data.path)
        if not pm.isNull():
            return pm.scaled(THUMB_MAX_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        try:
            img = Image.open(page_data.path)
            img = _prepare_image_for_thumb(img)
            qim = ImageQt(img)
            return QPixmap.fromImage(qim)
        except Exception:
            return _broken_pixmap()
    else:
        # PDF page
        try:
            pdf = pdfium.PdfDocument(page_data.path)
            page = pdf.get_page(page_data.page_index or 0)
            # Determine scale based on desired thumb max size
            width, height = page.get_size()
            scale = min(THUMB_MAX_SIZE.width() / max(width, 1), THUMB_MAX_SIZE.height() / max(height, 1))
            scale = max(scale, 0.2)
            bitmap = page.render(scale=scale * 2.0)  # render at 2x for sharper thumbnail
            pil_img = bitmap.to_pil()
            page.close()
            pil_img = _prepare_image_for_thumb(pil_img)
            qim = ImageQt(pil_img)
            pm = QPixmap.fromImage(qim)
            pdf.close()
            return pm
        except Exception:
            return _broken_pixmap()


def _prepare_image_for_thumb(img: Image.Image) -> Image.Image:
    # Ensure we have RGB for stable conversion
    if img.mode not in ("RGB", "RGBA"):
        img = img.convert("RGB")
    img.thumbnail((THUMB_MAX_SIZE.width(), THUMB_MAX_SIZE.height()), Image.Resampling.LANCZOS)
    return img


def _broken_pixmap() -> QPixmap:
    pm = QPixmap(THUMB_MAX_SIZE)
    pm.fill(Qt.GlobalColor.lightGray)
    return pm


def image_to_pdf_bytes(path: str) -> bytes:
    img = Image.open(path)
    if img.mode in ("P", "LA", "RGBA"):
        img = img.convert("RGB")
    buf = io.BytesIO()
    # Use higher resolution for better quality pages
    img.save(buf, format="PDF", resolution=300.0)
    return buf.getvalue()


def main():
    app = QApplication(sys.argv)
    win = MainWindow()
    win.show()
    sys.exit(app.exec())
