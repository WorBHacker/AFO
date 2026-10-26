"""Хаб → Бюджеты…: the budgets with what they have used, and a form to add, change or remove one (D12)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from . import presenter

if TYPE_CHECKING:
    from .window import MainWindow

COLUMNS = ["Бюджет", "Потрачено", "Предупредить с", "Лимит", "При лимите"]
STATE_MARKS = {"ok": "", "soft": "  ⚠", "hard": "  ⛔"}


def _money_box() -> QDoubleSpinBox:
    box = QDoubleSpinBox()
    box.setRange(0, 100_000)
    box.setDecimals(2)
    box.setSingleStep(1)
    box.setPrefix("$")
    box.setSpecialValueText("нет")  # 0 means "no such limit"
    return box


class BudgetDialog(QDialog):
    def __init__(self, window: MainWindow):
        super().__init__(window)
        self.window = window
        self.budgets: list[dict[str, Any]] = []
        self.setWindowTitle("Бюджеты")
        self.resize(720, 420)
        layout = QVBoxLayout(self)
        intro = QLabel(
            "Считаются только платные (облачные) модели, локальные бесплатны. Порог предупреждения даёт уведомление; "
            "после лимита новый платный запрос уходит только с вашего подтверждения или не уходит вовсе."
        )
        intro.setWordWrap(True)
        intro.setObjectName("msgFooter")
        layout.addWidget(intro)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(COLUMNS)
        self.table.verticalHeader().hide()
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.table.setVerticalScrollMode(QAbstractItemView.ScrollMode.ScrollPerPixel)
        self.table.itemSelectionChanged.connect(self._load_selected)
        layout.addWidget(self.table, 1)

        form = QGridLayout()
        self.scope_box = QComboBox()
        self.scope_box.addItem("Все платные модели", "total:")  # "scope:provider" - Qt matches strings, not tuples
        for ref in self._providers():
            self.scope_box.addItem(presenter.provider_name(ref), f"provider:{ref}")
        self.period_box = QComboBox()
        for value, label in presenter.PERIODS:
            self.period_box.addItem(label, value)
        self.period_box.setCurrentIndex(2)  # a month
        self.soft_box = _money_box()
        self.hard_box = _money_box()
        self.action_box = QComboBox()
        for value, label in presenter.HARD_ACTIONS:
            self.action_box.addItem(label, value)
        for row, (label, widget) in enumerate(
            [
                ("Для", self.scope_box),
                ("Период", self.period_box),
                ("Предупредить с", self.soft_box),
                ("Лимит", self.hard_box),
                ("После лимита", self.action_box),
            ]
        ):
            form.addWidget(QLabel(label), row // 3, (row % 3) * 2, Qt.AlignmentFlag.AlignRight)
            form.addWidget(widget, row // 3, (row % 3) * 2 + 1)
        layout.addLayout(form)

        self.error_label = QLabel()
        self.error_label.setObjectName("msgError")
        self.error_label.setWordWrap(True)
        layout.addWidget(self.error_label)

        buttons = QHBoxLayout()
        self.save_button = QPushButton("Сохранить")
        self.save_button.setObjectName("sendButton")
        self.save_button.clicked.connect(self.save)
        self.delete_button = QPushButton("Удалить выбранный")
        self.delete_button.clicked.connect(self.delete_selected)
        close = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        close.rejected.connect(self.reject)
        buttons.addWidget(self.save_button)
        buttons.addWidget(self.delete_button)
        buttons.addStretch(1)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.refresh()

    def _providers(self) -> list[str]:
        """Providers that have paid models now, and those that already have a budget."""
        paid = {m.get("provider") or m["id"].partition("/")[0] for m in self.window.models.values() if m.get("prices")}
        budgeted = {b["scope_ref"] for b in self.window.budgets if b["scope"] == "provider"}
        return sorted(paid | budgeted)

    def refresh(self) -> None:
        self.window._call(lambda: self.window.client.budgets(), self._fill)

    def _fill(self, budgets: list[dict[str, Any]]) -> None:
        self.budgets = budgets
        self.window._set_budgets(budgets)
        self.table.setRowCount(len(budgets))
        for row, budget in enumerate(budgets):
            cells = [
                presenter.budget_title(budget) + STATE_MARKS.get(budget["state"], ""),
                presenter.money(budget["spent_usd"]),
                presenter.money(budget["soft_usd"]) if budget["soft_usd"] is not None else "—",
                presenter.money(budget["hard_usd"]) if budget["hard_usd"] is not None else "—",
                dict(presenter.HARD_ACTIONS).get(budget["hard_action"], budget["hard_action"]),
            ]
            for column, text in enumerate(cells):
                self.table.setItem(row, column, QTableWidgetItem(text))

    def _selected(self) -> dict[str, Any] | None:
        rows = self.table.selectionModel().selectedRows()
        return self.budgets[rows[0].row()] if rows else None

    def _load_selected(self) -> None:
        budget = self._selected()
        if budget is None:
            return
        self.scope_box.setCurrentIndex(max(0, self.scope_box.findData(f"{budget['scope']}:{budget['scope_ref']}")))
        self.period_box.setCurrentIndex(self.period_box.findData(budget["period"]))
        self.soft_box.setValue(budget["soft_usd"] or 0)
        self.hard_box.setValue(budget["hard_usd"] or 0)
        self.action_box.setCurrentIndex(self.action_box.findData(budget["hard_action"]))

    def save(self) -> None:
        scope, _, provider = self.scope_box.currentData().partition(":")
        body = {
            "scope": scope,
            "provider": provider,
            "period": self.period_box.currentData(),
            "soft_usd": self.soft_box.value() or None,
            "hard_usd": self.hard_box.value() or None,
            "hard_action": self.action_box.currentData(),
        }
        self.error_label.clear()
        self.window._call(
            lambda: self.window.client.set_budget(**body),
            lambda _saved: self.refresh(),
            lambda error: self.error_label.setText(str(error)),
        )

    def delete_selected(self) -> None:
        budget = self._selected()
        if budget is None:
            self.error_label.setText("Выберите бюджет в таблице")
            return
        self.window._call(lambda: self.window.client.delete_budget(budget["id"]), lambda _r: self.refresh())
