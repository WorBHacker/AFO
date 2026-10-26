"""The chat's building blocks: message cards in a smoothly scrolling column, and the composer.

A message is a card of segments - prose labels and code blocks - so a streamed answer re-renders only the
segment that grew, and each code block is a real widget with a header, a copy button and room for a diff view.
"""

from __future__ import annotations

import math

from PySide6.QtCore import QEasingCurve, QSize, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QGuiApplication, QKeyEvent, QTextOption, QWheelEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QStackedWidget,
    QTextBrowser,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from . import palette
from .render import Code, Prose, highlight_code, language_label, page, render_plain, render_segments
from .theme import mono_font

WHEEL_STEP_PX = 22  # per wheel line; Windows scrolls 3 lines a notch
SMOOTH_SCROLL_MS = 160
COPIED_FEEDBACK_MS = 1500


def _label(name: str, *, selectable: bool = True) -> QLabel:
    label = QLabel()
    label.setObjectName(name)
    label.setWordWrap(True)
    label.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Minimum)
    if selectable:
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextBrowserInteraction)
    return label


def seconds(value: float) -> str:
    return f"{value:.1f} с" if value < 60 else f"{int(value // 60)} мин {int(value % 60)} с"


class ThinkingBlock(QFrame):
    """The model's reasoning as an accordion: a muted header with a timer, the text folded away until asked."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("thinking")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        self.header = QToolButton()
        self.header.setObjectName("thinkingHeader")
        self.header.setCheckable(True)
        self.header.setCursor(Qt.CursorShape.PointingHandCursor)
        self.header.toggled.connect(self._toggled)
        self.body = _label("thinkingBody")
        self.panel = QWidget()  # the folded part: a hairline rail and the text
        row = QHBoxLayout(self.panel)
        row.setContentsMargins(4, 0, 0, 0)
        row.setSpacing(12)
        rail = QFrame()
        rail.setObjectName("thinkingRail")
        rail.setFixedWidth(2)
        row.addWidget(rail)
        row.addWidget(self.body, 1)
        self.panel.hide()
        layout.addWidget(self.header, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.panel)
        self._text = ""
        self._elapsed: float | None = None
        self._running = False
        self._refresh()

    @property
    def expanded(self) -> bool:
        return self.header.isChecked()

    def set_text(self, text: str) -> None:
        if text == self._text:
            return
        self._text = text
        if self.expanded:
            self.body.setText(self._html())

    def set_elapsed(self, value: float | None, *, running: bool = False) -> None:
        self._elapsed, self._running = value, running
        self._refresh()

    def _refresh(self) -> None:
        chevron = "▾" if self.expanded else "▸"
        if self._running:
            title = f"Думает… {seconds(self._elapsed or 0)}"
        elif self._elapsed is not None:
            title = f"Размышления · {seconds(self._elapsed)}"
        else:
            title = "Размышления"
        self.header.setText(f"{chevron}  {title}")

    def _html(self) -> str:
        # muted and a size down: the page's own style sheet would otherwise give it the answer's colour
        return page(f'<div style="color:{palette.MUTED}; font-size:9pt;">{render_plain(self._text)}</div>')

    def _toggled(self, shown: bool) -> None:
        if shown:
            self.body.setText(self._html())
        self.panel.setVisible(shown)
        self._refresh()


class CodeView(QTextBrowser):
    """Highlighted code without wrapping: as tall as its text, scrolls sideways when lines are long."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("codeView")
        self.setFont(mono_font())
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setLineWrapMode(QTextEdit.LineWrapMode.NoWrap)
        self.setWordWrapMode(QTextOption.WrapMode.NoWrap)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setOpenLinks(False)
        self.document().setDocumentMargin(10)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._height = 0

    def set_highlighted(self, spans_html: str) -> None:
        self.setHtml(
            f'<pre style="margin:0; font-family:{palette.MONO_CSS}; line-height:{palette.LINE_HEIGHT};">'
            f"{spans_html}</pre>"
        )
        self._fit_height()

    def _fit_height(self) -> None:
        height = math.ceil(self.document().size().height())
        if self.document().idealWidth() > self.viewport().width():
            height += palette.SCROLLBAR + 2
        self._height = height + 2 * self.frameWidth()
        self.setFixedHeight(self._height)

    def sizeHint(self) -> QSize:
        # a text browser hints ~192px tall by default; the stack around it would keep that empty space
        return QSize(super().sizeHint().width(), self._height)

    def minimumSizeHint(self) -> QSize:
        return QSize(super().minimumSizeHint().width(), self._height)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_height()

    def wheelEvent(self, event: QWheelEvent) -> None:
        delta = event.angleDelta()
        if abs(delta.y()) > abs(delta.x()) and not event.modifiers() & Qt.KeyboardModifier.ShiftModifier:
            event.ignore()  # a vertical wheel scrolls the conversation, not this block
            return
        super().wheelEvent(event)


class CodeBlock(QFrame):
    """A code block: header (language, actions) over the code.

    The body is a stack so a diff view can join the code view later (write approvals show a diff, D17); its
    button sits in the header, hidden until then.
    """

    copied = Signal()

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("codeBlock")
        self.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(1, 1, 1, 1)
        layout.setSpacing(0)

        header = QWidget()
        header.setObjectName("codeHeader")
        header.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        row = QHBoxLayout(header)
        row.setContentsMargins(10, 3, 4, 3)
        row.setSpacing(4)
        self.lang = QLabel()
        self.lang.setObjectName("codeLang")
        self.diff_button = self._action("Diff")
        self.diff_button.setToolTip("Сравнение версий — появится вместе с подтверждением правок")
        self.diff_button.hide()
        self.copy_button = self._action("Копировать")
        self.copy_button.clicked.connect(self.copy)
        row.addWidget(self.lang)
        row.addStretch(1)
        row.addWidget(self.diff_button)
        row.addWidget(self.copy_button)

        self.pages = QStackedWidget()
        self.pages.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)  # as tall as the code
        self.view = CodeView()
        self.pages.addWidget(self.view)  # page 0: the code; a diff view becomes page 1
        layout.addWidget(header)
        layout.addWidget(self.pages)

        self.code = ""
        self._lang = None
        self._reset = QTimer(self)
        self._reset.setSingleShot(True)
        self._reset.setInterval(COPIED_FEEDBACK_MS)
        self._reset.timeout.connect(lambda: self.copy_button.setText("Копировать"))

    def _action(self, text: str) -> QToolButton:
        button = QToolButton()
        button.setObjectName("codeAction")
        button.setText(text)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        return button

    def set_code(self, lang: str, code: str) -> None:
        if lang != self._lang:
            self._lang = lang
            self.lang.setText(language_label(lang))
        if code != self.code:
            self.code = code
            self.view.set_highlighted(highlight_code(code, lang))

    def copy(self) -> None:
        QGuiApplication.clipboard().setText(self.code)
        self.copy_button.setText("Скопировано ✓")
        self._reset.start()
        self.copied.emit()


class MessageWidget(QFrame):
    """A message card: header, reasoning accordion, a status line (model loading), segments, footer."""

    copied = Signal()
    link_activated = Signal(str)

    def __init__(self, role: str, title: str, parent: QWidget | None = None):
        super().__init__(parent)
        self.role = role
        self.setObjectName(f"msg_{role}")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(6)
        self.header = _label("msgHeader", selectable=False)
        self.header.setText(title)
        self.thinking = ThinkingBlock()
        self.thinking.hide()
        self.notice = _label("msgNotice", selectable=False)
        self.notice.hide()
        self._content = QVBoxLayout()
        self._content.setSpacing(8)
        self.footer = _label("msgFooter", selectable=False)
        self.footer.hide()
        layout.addWidget(self.header)
        layout.addWidget(self.thinking)
        layout.addWidget(self.notice)
        layout.addLayout(self._content)
        layout.addWidget(self.footer)
        self._segments: list[QLabel | CodeBlock] = []
        self._shown: list[Prose | Code] = []

    # --- content ---

    def set_markdown(self, text: str) -> None:
        self._show(render_segments(text) if text else [])

    def set_plain(self, text: str) -> None:
        self._show([Prose(render_plain(text))] if text else [])

    def _show(self, segments: list[Prose | Code]) -> None:
        """Updates only what changed: while an answer streams, that is the last segment."""
        for i, segment in enumerate(segments):
            if i < len(self._shown) and self._shown[i] == segment:
                continue
            widget = self._segments[i] if i < len(self._segments) else None
            if isinstance(segment, Prose):
                if not isinstance(widget, QLabel):
                    widget = self._replace(i, self._prose_label())
                widget.setText(page(segment.html))
            else:
                if not isinstance(widget, CodeBlock):
                    widget = self._replace(i, CodeBlock())
                    widget.copied.connect(self.copied)
                widget.set_code(segment.lang, segment.code)
        for widget in self._segments[len(segments) :]:
            self._content.removeWidget(widget)
            widget.deleteLater()
        del self._segments[len(segments) :]
        self._shown = list(segments)

    def _prose_label(self) -> QLabel:
        label = _label("msgError" if self.role == "error" else "msgBody")
        label.setOpenExternalLinks(False)
        label.linkActivated.connect(self.link_activated)
        return label

    def _replace(self, index: int, widget: QWidget) -> QWidget:
        if index < len(self._segments):
            old = self._segments[index]
            self._content.replaceWidget(old, widget)
            old.deleteLater()
            self._segments[index] = widget
        else:
            self._content.addWidget(widget)
            self._segments.append(widget)
        return widget

    def prose_html(self) -> str:
        return "".join(w.text() for w in self._segments if isinstance(w, QLabel))

    def code_blocks(self) -> list[CodeBlock]:
        return [w for w in self._segments if isinstance(w, CodeBlock)]

    @property
    def has_content(self) -> bool:
        return bool(self._segments)

    # --- the rest of the card ---

    def set_reasoning(self, text: str, elapsed: float | None = None, *, running: bool = False) -> None:
        self.thinking.setVisible(bool(text) or running)
        self.thinking.set_text(text)
        self.thinking.set_elapsed(elapsed, running=running)

    def set_notice(self, text: str) -> None:
        self.notice.setText(text)
        self.notice.setVisible(bool(text))

    def set_header(self, text: str) -> None:
        self.header.setText(text)

    def set_footer(self, text: str) -> None:
        self.footer.setText(text)
        self.footer.setVisible(bool(text))


class _Column(QWidget):
    """The transcript's content. A scroll area sizes its widget by sizeHint, and a word-wrapped label hints the
    height it would need at some other width, so the column would end in empty space; hint the height that the
    current width really needs."""

    def _fitted(self, hint: QSize) -> QSize:
        if self.width() > 0 and self.layout() is not None and self.layout().hasHeightForWidth():
            return QSize(hint.width(), self.heightForWidth(self.width()))
        return hint

    def sizeHint(self) -> QSize:
        return self._fitted(super().sizeHint())

    def minimumSizeHint(self) -> QSize:
        return self._fitted(super().minimumSizeHint())


class Transcript(QScrollArea):
    """Message cards top to bottom; pixel scrolling eased on mouse wheels; follows new output at the bottom."""

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        container = _Column()
        container.setObjectName("transcriptColumn")
        self._column = QVBoxLayout(container)
        # the default constraint would pin the column's minimum height to the labels' over-tall minimum hints
        self._column.setSizeConstraint(QVBoxLayout.SizeConstraint.SetNoConstraint)
        self._column.setContentsMargins(20, 16, 20, 16)
        self._column.setSpacing(12)
        self._column.addStretch(1)
        self.setWidget(container)
        self._follow = True
        bar = self.verticalScrollBar()
        bar.setSingleStep(WHEEL_STEP_PX)
        bar.valueChanged.connect(self._on_scrolled)
        bar.rangeChanged.connect(self._on_range)
        self._animation = QVariantAnimation(self)
        self._animation.setDuration(SMOOTH_SCROLL_MS)
        self._animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._animation.valueChanged.connect(lambda value: bar.setValue(round(value)))

    def messages(self) -> list[MessageWidget]:
        items = (self._column.itemAt(i).widget() for i in range(self._column.count()))
        return [w for w in items if isinstance(w, MessageWidget)]

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

    def wheelEvent(self, event: QWheelEvent) -> None:
        lines = event.angleDelta().y() / 120
        if not event.pixelDelta().isNull() or not lines or event.modifiers():
            super().wheelEvent(event)  # touchpads send pixels and are smooth already
            return
        bar = self.verticalScrollBar()
        running = self._animation.state() == QVariantAnimation.State.Running
        start = self._animation.endValue() if running else bar.value()
        step = WHEEL_STEP_PX * QApplication.wheelScrollLines()
        target = max(bar.minimum(), min(bar.maximum(), round(start - lines * step)))
        self._animation.stop()
        self._animation.setStartValue(bar.value())
        self._animation.setEndValue(target)
        self._animation.start()
        event.accept()

    def _on_scrolled(self, value: int) -> None:
        self._follow = value >= self.verticalScrollBar().maximum() - 40

    def _on_range(self, _minimum: int, maximum: int) -> None:
        if self._follow and self._animation.state() != QVariantAnimation.State.Running:
            self.verticalScrollBar().setValue(maximum)


class Composer(QTextEdit):
    """Plain-text input that grows with its text (up to a limit). Enter sends, Shift+Enter starts a new line."""

    submitted = Signal()
    MIN_HEIGHT = 40
    MAX_HEIGHT = 200

    def __init__(self, parent: QWidget | None = None):
        super().__init__(parent)
        self.setObjectName("composer")
        self.setAcceptRichText(False)
        self.setTabChangesFocus(True)
        self.setPlaceholderText("Сообщение… (Enter — отправить, Shift+Enter — новая строка)")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setFrameShape(QFrame.Shape.NoFrame)
        self.verticalScrollBar().setSingleStep(WHEEL_STEP_PX)
        self.document().contentsChanged.connect(self._fit)
        self._fit()

    def _fit(self) -> None:
        chrome = self.height() - self.viewport().height() if self.isVisible() else 8
        wanted = math.ceil(self.document().size().height()) + chrome
        self.setFixedHeight(max(self.MIN_HEIGHT, min(self.MAX_HEIGHT, wanted)))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        self._fit()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self._fit()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)
