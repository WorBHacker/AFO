"""The chat's building blocks: one widget per message in a scrolling column, and the message composer.

One widget per message means a streamed answer re-renders only itself, not the whole conversation.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QGuiApplication, QKeyEvent
from PySide6.QtWidgets import (
    QFrame,
    QLabel,
    QPlainTextEdit,
    QScrollArea,
    QSizePolicy,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from .presenter import markdown_html, plain_html


def _label(name: str, *, selectable: bool = True) -> QLabel:
    label = QLabel()
    label.setObjectName(name)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    if selectable:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    return label


class MessageWidget(QFrame):
    """A message: header, collapsible reasoning, a status line (model loading), the body, and a footer."""

    copied = Signal()
    link_activated = Signal(str)  # a link in the text other than 'Копировать'

    def __init__(self, role: str, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.role = role
        self.setObjectName(f"msg_{role}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(4)

        self.header = _label("msgHeader", selectable=False)
        self.header.setText(title)
        self.think_toggle = QToolButton()
        self.think_toggle.setObjectName("thinkToggle")
        self.think_toggle.setCheckable(True)
        self.think_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.think_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.think_toggle.toggled.connect(self._toggle_reasoning)
        self.think_toggle.hide()
        self.think = _label("msgThink")
        self.think.setTextFormat(Qt.TextFormat.PlainText)
        self.think.hide()
        self.notice = _label("msgNotice", selectable=False)
        self.notice.hide()
        self.body = _label("msgError" if role == "error" else "msgBody")
        self.body.setOpenExternalLinks(False)
        self.body.linkActivated.connect(self._on_link)
        self.footer = _label("msgFooter", selectable=False)
        self.footer.hide()
        for widget in (self.header, self.think_toggle, self.think, self.notice, self.body, self.footer):
            layout.addWidget(widget)

        self._reasoning = ""
        self._codes: list[str] = []

    @property
    def text(self) -> str:
        return self.body.text()

    def set_text(self, text: str, *, markdown: bool = True) -> None:
        if markdown:
            html, self._codes = markdown_html(text)
        else:
            html, self._codes = plain_html(text), []
        self.body.setText(html if text else "")
        self.body.setVisible(bool(text))

    def set_reasoning(self, reasoning: str, *, streaming: bool = False) -> None:
        self._reasoning = reasoning
        if not reasoning:
            self.think_toggle.hide()
            return
        suffix = " …" if streaming else ""
        self.think_toggle.setText(f"Размышления ({len(reasoning)} симв.){suffix}")
        self.think_toggle.show()
        if self.think_toggle.isChecked():
            self.think.setText(reasoning)

    def set_notice(self, text: str) -> None:
        self.notice.setText(text)
        self.notice.setVisible(bool(text))

    def set_header(self, text: str) -> None:
        self.header.setText(text)

    def set_footer(self, text: str) -> None:
        self.footer.setText(text)
        self.footer.setVisible(bool(text))

    def _toggle_reasoning(self, shown: bool) -> None:
        self.think_toggle.setArrowType(Qt.ArrowType.DownArrow if shown else Qt.ArrowType.RightArrow)
        if shown:
            self.think.setText(self._reasoning)
        self.think.setVisible(shown)

    def _on_link(self, href: str) -> None:
        if href.startswith("copy:"):
            index = int(href.removeprefix("copy:"))
            if 0 <= index < len(self._codes):
                QGuiApplication.clipboard().setText(self._codes[index])
                self.copied.emit()
            return
        self.link_activated.emit(href)


class Transcript(QScrollArea):
    """Messages top to bottom. Follows new output while the view is at the bottom, stays put otherwise."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = QWidget()
        self._column = QVBoxLayout(container)
        self._column.setContentsMargins(16, 12, 16, 12)
        self._column.setSpacing(10)
        self._column.addStretch(1)
        self.setWidget(container)
        self._follow = True
        bar = self.verticalScrollBar()
        bar.valueChanged.connect(self._on_scrolled)
        bar.rangeChanged.connect(self._on_range)

    def messages(self) -> list[MessageWidget]:
        return [
            self._column.itemAt(i).widget()
            for i in range(self._column.count())
            if isinstance(self._column.itemAt(i).widget(), MessageWidget)
        ]

    def add(self, widget: MessageWidget) -> MessageWidget:
        self._column.insertWidget(self._column.count() - 1, widget)
        return widget

    def remove(self, widget: MessageWidget) -> None:
        self._column.removeWidget(widget)
        widget.deleteLater()

    def clear(self) -> None:
        for widget in self.messages():
            self.remove(widget)
        self._follow = True

    def _on_scrolled(self, value: int) -> None:
        self._follow = value >= self.verticalScrollBar().maximum() - 40

    def _on_range(self, _minimum: int, maximum: int) -> None:
        if self._follow:
            self.verticalScrollBar().setValue(maximum)


class Composer(QPlainTextEdit):
    """Enter sends, Shift+Enter starts a new line."""

    submitted = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setPlaceholderText("Сообщение… (Enter — отправить, Shift+Enter — новая строка)")
        self.setMaximumHeight(110)
        self.setTabChangesFocus(True)

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)
