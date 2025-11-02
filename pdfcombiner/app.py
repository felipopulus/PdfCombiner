from __future__ import annotations

import io
import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from typing import List, Optional, Tuple

from PyQt6.QtCore import Qt, QSize, QUrl
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QVBoxLayout,
    QHBoxLayout,
    QPushButton,
    QLabel,
    QListWidget,
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
        self.setResizeMode(QListWidget.ResizeMode.Adjust)
        self.setMovement(QListWidget.Movement.Snap)
        self.setWrapping(True)
        self.setSpacing(10)
        self.setDragEnabled(True)
        self.setAcceptDrops(True)
        self.setDropIndicatorShown(True)
        self.setDragDropMode(QListWidget.DragDropMode.InternalMove)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            # Accept only if at least one of the urls is a supported file
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            if any(is_supported_path(p) for p in paths):
                event.acceptProposedAction()
                return
        super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
            return
        super().dragMoveEvent(event)

    def dropEvent(self, event):
        if event.mimeData().hasUrls():
            paths = [u.toLocalFile() for u in event.mimeData().urls()]
            self.parent().add_files(paths)  # type: ignore[attr-defined]
            event.acceptProposedAction()
            return
        super().dropEvent(event)


class PageItemWidget(QWidget):
    def __init__(self, page_data: PageData, thumb: QPixmap, delete_callback):
        super().__init__()
        self.page_data = page_data

        layout = QVBoxLayout(self)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        self.thumb_label = QLabel()
        self.thumb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumb_label.setFixedSize(THUMB_MAX_SIZE)
        self.thumb_label.setPixmap(thumb.scaled(THUMB_MAX_SIZE, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
        layout.addWidget(self.thumb_label)

        bottom = QHBoxLayout()
        bottom.setContentsMargins(0, 0, 0, 0)
        bottom.setSpacing(6)

        self.name_label = QLabel(page_data.label)
        self.name_label.setToolTip(page_data.label)
        self.name_label.setWordWrap(True)
        bottom.addWidget(self.name_label, 1)

        self.del_btn = QPushButton()
        self.del_btn.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
        self.del_btn.setToolTip("Remove this page")
        self.del_btn.setFixedSize(QSize(28, 28))
        self.del_btn.clicked.connect(delete_callback)  # type: ignore[arg-type]
        bottom.addWidget(self.del_btn, 0)

        layout.addLayout(bottom)


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
        # A slightly larger size hint to accommodate label and button
        item.setSizeHint(QSize(THUMB_MAX_SIZE.width() + 24, THUMB_MAX_SIZE.height() + 56))
        item.setData(Qt.ItemDataRole.UserRole, page_data)

        def delete_this():
            row = self.list.row(item)
            if row >= 0:
                self.list.takeItem(row)
                self.statusBar().showMessage(f"Total pages: {self.list.count()}")

        widget = PageItemWidget(page_data, thumb, delete_this)
        self.list.addItem(item)
        self.list.setItemWidget(item, widget)

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

    @lru_cache(maxsize=32)
    def _get_reader(self, path: str) -> pypdf.PdfReader:
        return pypdf.PdfReader(path)


def is_supported_path(path: str) -> bool:
    ext = os.path.splitext(path)[1].lower()
    return ext in SUPPORTED_IMAGE_EXTS or ext in PDF_EXTS


@lru_cache(maxsize=256)
def get_thumbnail(page_data: PageData) -> QPixmap:
    if page_data.kind == "img":
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
