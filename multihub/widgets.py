from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QMimeData, QPoint, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QColor, QDrag, QMouseEvent, QPixmap
from PyQt6.QtMultimedia import QAudioOutput, QMediaPlayer
from PyQt6.QtMultimediaWidgets import QVideoWidget
from PyQt6.QtWidgets import (
    QFrame,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSizePolicy,
    QStackedLayout,
    QVBoxLayout,
    QWidget,
)

from .models import SourceItem


SOURCE_MIME = "application/x-multihub-source-id"
SAFE_PREVIEW_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
MAX_PREVIEW_FILE_SIZE_BYTES = 25 * 1024 * 1024


def card_shadow(widget: QWidget, blur: int = 32) -> None:
    effect = QGraphicsDropShadowEffect(widget)
    effect.setBlurRadius(blur)
    effect.setOffset(0, 18)
    effect.setColor(QColor(0, 0, 0, 110))
    widget.setGraphicsEffect(effect)


def source_badge(mime_type: str) -> str:
    if mime_type.startswith("video/"):
        return "VID"
    if mime_type.startswith("image/"):
        return "IMG"
    if mime_type.startswith("audio/"):
        return "AUD"
    if mime_type == "application/pdf":
        return "PDF"
    if "presentation" in mime_type or "powerpoint" in mime_type or "ppt" in mime_type:
        return "PPT"
    return "SRC"


def source_label(mime_type: str, demo: bool) -> str:
    if mime_type.startswith("video/"):
        kind = "Video"
    elif mime_type.startswith("image/"):
        kind = "Image"
    elif mime_type.startswith("audio/"):
        kind = "Audio"
    elif mime_type == "application/pdf":
        kind = "PDF document"
    elif "presentation" in mime_type or "powerpoint" in mime_type or "ppt" in mime_type:
        kind = "Presentation"
    else:
        kind = "Source file"
    return f"{kind} demo source" if demo else kind


class SourceListWidget(QListWidget):
    preview_requested = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setSpacing(10)
        self.setSelectionMode(QListWidget.SelectionMode.SingleSelection)
        self.setDragEnabled(True)
        self.setProperty("class", "source-list")
        self.itemClicked.connect(self._emit_preview)

    def _emit_preview(self, item: QListWidgetItem) -> None:
        source_id = item.data(Qt.ItemDataRole.UserRole)
        if source_id:
            self.preview_requested.emit(str(source_id))

    def add_source(self, source: SourceItem) -> None:
        item = QListWidgetItem()
        item.setSizeHint(QSize(0, 76))
        item.setData(Qt.ItemDataRole.UserRole, source.source_id)
        item.setToolTip(source.name)
        self.insertItem(0, item)
        self.setItemWidget(item, SourceCard(source))

    def remove_source(self, source_id: str) -> None:
        for index in range(self.count()):
            item = self.item(index)
            if item.data(Qt.ItemDataRole.UserRole) != source_id:
                continue
            self.takeItem(index)
            return

    def selected_source_id(self) -> str | None:
        item = self.currentItem()
        if item is None:
            return None
        value = item.data(Qt.ItemDataRole.UserRole)
        return str(value) if value else None

    def startDrag(self, supported_actions: Qt.DropAction) -> None:
        item = self.currentItem()
        if item is None:
            return
        source_id = item.data(Qt.ItemDataRole.UserRole)
        if not source_id:
            return

        mime = QMimeData()
        mime.setData(SOURCE_MIME, str(source_id).encode("utf-8"))
        drag = QDrag(self)
        drag.setMimeData(mime)
        drag.exec(Qt.DropAction.CopyAction)


class SourceCard(QFrame):
    def __init__(self, source: SourceItem, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("sourceCard")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(12)

        badge = QLabel(source_badge(source.mime_type))
        badge.setObjectName("sourceBadge")
        badge.setAlignment(Qt.AlignmentFlag.AlignCenter)
        badge.setFixedSize(46, 46)
        preview = _source_preview_pixmap(source, QSize(46, 46))
        if preview is not None:
            badge.setPixmap(preview)
            badge.setText("")

        name = QLabel(source.name)
        name.setObjectName("sourceName")
        meta = QLabel(source_label(source.mime_type, source.demo))
        meta.setObjectName("sourceMeta")

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)
        text_layout.addWidget(name)
        text_layout.addWidget(meta)

        layout.addWidget(badge)
        layout.addLayout(text_layout, 1)


class DragNode(QFrame):
    source_dropped = pyqtSignal(str, str)
    selected = pyqtSignal(str, bool)

    def __init__(
        self,
        marker_id: str,
        title: str,
        subtitle: str,
        endpoint_id: str | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.marker_id = marker_id
        self.endpoint_id = endpoint_id
        self.setObjectName("deviceNode")
        self.setAcceptDrops(True)
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        self._drag_offset = QPoint()
        self._drag_started = False
        self._source_name: str | None = None
        self._start_offset_seconds = 0.0
        self.setFixedSize(118, 62)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(0)

        self.preview = QLabel("DROP")
        self.preview.setObjectName("devicePreview")
        self.preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview.setMinimumHeight(64)
        self.preview.hide()

        self.title_label = QLabel(title)
        self.title_label.setObjectName("deviceTitle")
        self.title_label.setWordWrap(True)
        self.title_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.subtitle_label = QLabel(subtitle)
        self.subtitle_label.setObjectName("deviceSubtitle")
        self.subtitle_label.setWordWrap(True)
        self.subtitle_label.hide()

        layout.addWidget(self.title_label, 1)
        self._refresh_tooltip()

    def set_endpoint(self, title: str, subtitle: str, endpoint_id: str | None = None) -> None:
        self.title_label.setText(title)
        self.subtitle_label.setText(subtitle)
        self.endpoint_id = endpoint_id
        self._refresh_tooltip()

    def set_source(self, source: SourceItem | None) -> None:
        if source is None:
            self._source_name = None
            self.preview.setPixmap(QPixmap())
            self.preview.setText("DROP")
            self._refresh_tooltip()
            return

        self._source_name = source.name
        pixmap = _source_preview_pixmap(source, QSize(92, 60))
        if pixmap is not None:
            self.preview.setPixmap(pixmap)
            self.preview.setText("")
        else:
            self.preview.setPixmap(QPixmap())
            self.preview.setText(source_badge(source.mime_type))
        self._refresh_tooltip()

    def set_start_offset(self, start_offset_seconds: float) -> None:
        self._start_offset_seconds = max(0.0, float(start_offset_seconds))
        self._refresh_tooltip()

    def set_selected(self, selected: bool) -> None:
        self.setProperty("selected", selected)
        self.style().unpolish(self)
        self.style().polish(self)

    def _refresh_tooltip(self) -> None:
        tooltip = self.title_label.text()
        if self._source_name:
            tooltip = f"{tooltip}\nSource: {self._source_name}"
        if self._start_offset_seconds > 0.0:
            tooltip = f"{tooltip}\nOffset: +{self._start_offset_seconds:.1f}s"
        self.setToolTip(tooltip)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(SOURCE_MIME):
            event.acceptProposedAction()
            self.setProperty("dropActive", True)
            self.style().unpolish(self)
            self.style().polish(self)
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self.setProperty("dropActive", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        self.setProperty("dropActive", False)
        self.style().unpolish(self)
        self.style().polish(self)
        source_id = bytes(event.mimeData().data(SOURCE_MIME)).decode("utf-8")
        self.source_dropped.emit(self.marker_id, source_id)
        event.acceptProposedAction()

    def mousePressEvent(self, event: QMouseEvent) -> None:
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.position().toPoint()
            self._drag_started = False
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            additive = bool(event.modifiers() & Qt.KeyboardModifier.ControlModifier)
            self.selected.emit(self.marker_id, additive)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent) -> None:
        if not (event.buttons() & Qt.MouseButton.LeftButton):
            return

        parent = self.parentWidget()
        if parent is None:
            return

        self._drag_started = True
        new_pos = self.mapToParent(event.position().toPoint() - self._drag_offset)
        clamped = QRect(12, 12, parent.width() - self.width() - 24, parent.height() - self.height() - 24)
        x = min(max(clamped.left(), new_pos.x()), clamped.right())
        y = min(max(clamped.top(), new_pos.y()), clamped.bottom())
        self.move(x, y)
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:
        self.setCursor(Qt.CursorShape.OpenHandCursor)
        event.accept()


class DocumentCard(QFrame):
    open_requested = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("documentCard")
        self._button = QPushButton("Open Source File")
        self._button.clicked.connect(self.open_requested.emit)

        self._title = QLabel("No source playing")
        self._title.setObjectName("documentTitle")
        self._title.setWordWrap(True)
        self._copy = QLabel("Drop a source onto the TV")
        self._copy.setObjectName("documentCopy")
        self._copy.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(34, 34, 34, 34)
        layout.setSpacing(14)
        layout.addStretch(1)
        layout.addWidget(self._title, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._copy, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addWidget(self._button, 0, Qt.AlignmentFlag.AlignHCenter)
        layout.addStretch(1)

    def set_content(self, title: str, body: str, allow_open: bool) -> None:
        self._title.setText(title)
        self._copy.setText(body)
        self._button.setVisible(allow_open)


class TVSurface(QFrame):
    source_dropped = pyqtSignal(str)

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setAcceptDrops(True)
        self.setObjectName("tvSurface")
        self._open_path: Path | None = None

        self.player = QMediaPlayer(self)
        self.audio_output = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_output)

        self.video_widget = QVideoWidget(self)
        self.video_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.player.setVideoOutput(self.video_widget)

        self.placeholder = QWidget()
        self.placeholder.setObjectName("tvPlaceholder")

        self.image_label = QLabel()
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image_label.setScaledContents(False)

        self.document_card = DocumentCard()

        self.stack = QStackedLayout(self)
        self.stack.setContentsMargins(18, 18, 18, 18)
        self.stack.addWidget(self.placeholder)
        self.stack.addWidget(self.image_label)
        self.stack.addWidget(self.video_widget)
        self.stack.addWidget(self.document_card)
        self.stack.setCurrentWidget(self.placeholder)

    def dragEnterEvent(self, event) -> None:  # type: ignore[override]
        if event.mimeData().hasFormat(SOURCE_MIME):
            event.acceptProposedAction()
            self.setProperty("dropActive", True)
            self.style().unpolish(self)
            self.style().polish(self)
            return
        event.ignore()

    def dragLeaveEvent(self, event) -> None:  # type: ignore[override]
        self.setProperty("dropActive", False)
        self.style().unpolish(self)
        self.style().polish(self)
        super().dragLeaveEvent(event)

    def dropEvent(self, event) -> None:  # type: ignore[override]
        self.setProperty("dropActive", False)
        self.style().unpolish(self)
        self.style().polish(self)
        source_id = bytes(event.mimeData().data(SOURCE_MIME)).decode("utf-8")
        self.source_dropped.emit(source_id)
        event.acceptProposedAction()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if self.stack.currentWidget() is self.image_label and self._open_path:
            self._show_image(self._open_path)

    def reset(self) -> None:
        self.player.setLoops(QMediaPlayer.Loops.Once)
        self.player.stop()
        self.image_label.clear()
        self._open_path = None
        self.stack.setCurrentWidget(self.placeholder)

    def preview_image(self, path: Path) -> None:
        self.player.setLoops(QMediaPlayer.Loops.Once)
        self.player.stop()
        self._open_path = path
        self._show_image(path)
        self.stack.setCurrentWidget(self.image_label)

    def _show_image(self, path: Path) -> None:
        pixmap = QPixmap(str(path))
        if pixmap.isNull():
            self.document_card.set_content(path.name, "The image could not be decoded for local preview.", True)
            self.stack.setCurrentWidget(self.document_card)
            return

        target_width = max(1, self.width() - 36)
        target_height = max(1, self.height() - 36)
        scaled = pixmap.scaled(
            target_width,
            target_height,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.image_label.setPixmap(scaled)

    def preview_video(self, path: Path) -> None:
        from PyQt6.QtCore import QUrl

        self._open_path = path
        self.player.setLoops(QMediaPlayer.Loops.Infinite)
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()
        self.stack.setCurrentWidget(self.video_widget)

    def preview_document(self, title: str, body: str, allow_open: bool) -> None:
        self.player.setLoops(QMediaPlayer.Loops.Once)
        self.player.stop()
        self._open_path = None
        self.document_card.set_content(title, body, allow_open)
        self.stack.setCurrentWidget(self.document_card)


class HubStage(QFrame):
    marker_source_dropped = pyqtSignal(str, str)
    marker_selected = pyqtSignal(str, bool)
    background_clicked = pyqtSignal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setObjectName("hubStage")
        self._nodes: dict[str, DragNode] = {}
        self._base_size = QSize(1160, 720)
        self._zoom = 1.0
        self.setFixedSize(self._base_size)

    @property
    def zoom(self) -> float:
        return self._zoom

    def set_zoom(self, zoom: float) -> None:
        bounded = max(0.6, min(2.2, zoom))
        if abs(bounded - self._zoom) < 0.001:
            return
        self._zoom = bounded
        scaled = QSize(
            int(self._base_size.width() * self._zoom),
            int(self._base_size.height() * self._zoom),
        )
        self.setFixedSize(scaled)
        self.updateGeometry()

    def add_device_node(
        self,
        marker_id: str,
        title: str,
        subtitle: str,
        position: QPoint,
        endpoint_id: str | None = None,
    ) -> DragNode:
        node = DragNode(marker_id, title, subtitle, endpoint_id=endpoint_id, parent=self)
        node.move(position)
        node.source_dropped.connect(self.marker_source_dropped.emit)
        node.selected.connect(self.marker_selected.emit)
        node.show()
        self._nodes[marker_id] = node
        return node

    def node(self, marker_id: str) -> DragNode | None:
        return self._nodes.get(marker_id)

    def remove_device_node(self, marker_id: str) -> None:
        node = self._nodes.pop(marker_id, None)
        if node is None:
            return
        node.setParent(None)
        node.deleteLater()

    def set_selected_markers(self, marker_ids: set[str]) -> None:
        for key, node in self._nodes.items():
            node.set_selected(key in marker_ids)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() == Qt.MouseButton.LeftButton and self.childAt(event.position().toPoint()) is None:
            self.background_clicked.emit()
        super().mousePressEvent(event)


def _source_preview_pixmap(source: SourceItem, size: QSize) -> QPixmap | None:
    if source.path and source.mime_type.startswith("image/"):
        suffix = source.path.suffix.lower()
        if suffix not in SAFE_PREVIEW_SUFFIXES:
            return None
        try:
            if not source.path.exists() or source.path.stat().st_size > MAX_PREVIEW_FILE_SIZE_BYTES:
                return None
        except OSError:
            return None

        pixmap = QPixmap(str(source.path))
        if pixmap.isNull():
            return None
        return pixmap.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation,
        )
    return None
