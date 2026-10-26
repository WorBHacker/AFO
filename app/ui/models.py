"""List models for QML. A list model updates one row at a time, so a streamed answer re-renders only itself."""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import QAbstractListModel, QByteArray, QModelIndex, QPersistentModelIndex, Qt

# never a role named "model": in a QML delegate it would hide the delegate's own `model` (the row's data)
ROLES = [
    "key",
    "role",
    "text",
    "html",
    "author",
    "local",
    "markdown",
    "segments",
    "thinking",
    "footer",
    "streaming",
    "notice",
]


class RowModel(QAbstractListModel):
    """Rows are dicts; QML reads their fields as roles (model.segments, model.thinking, ...)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict[str, Any]] = []

    def roleNames(self) -> dict[int, QByteArray]:
        return {Qt.ItemDataRole.UserRole + i: QByteArray(name.encode()) for i, name in enumerate(ROLES)}

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        offset = role - Qt.ItemDataRole.UserRole
        return self._rows[index.row()].get(ROLES[offset]) if 0 <= offset < len(ROLES) else None

    # --- for the controller ---

    @property
    def rows(self) -> list[dict[str, Any]]:
        return self._rows

    def reset(self, rows: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def append(self, row: dict[str, Any]) -> None:
        self.beginInsertRows(QModelIndex(), len(self._rows), len(self._rows))
        self._rows.append(row)
        self.endInsertRows()

    def find(self, key: str) -> int:
        return next((i for i, row in enumerate(self._rows) if row.get("key") == key), -1)

    def update(self, index: int, **changes: Any) -> None:
        self._rows[index] = {**self._rows[index], **changes}
        model_index = self.index(index, 0)
        roles = [Qt.ItemDataRole.UserRole + ROLES.index(name) for name in changes if name in ROLES]
        self.dataChanged.emit(model_index, model_index, roles)

    def remove(self, index: int) -> None:
        self.beginRemoveRows(QModelIndex(), index, index)
        del self._rows[index]
        self.endRemoveRows()
