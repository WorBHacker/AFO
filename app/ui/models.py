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
    "tools",
    "title",  # an error card's plain words (10.6)
    "hint",
]
TREE_ROLES = ["key", "name", "path", "depth", "dir", "open", "status", "size", "secret"]


class RowModel(QAbstractListModel):
    """Rows are dicts; QML reads their fields as roles (model.segments, model.thinking, ...)."""

    def __init__(self, parent=None, roles: list[str] | None = None):
        super().__init__(parent)
        self._roles = roles or ROLES
        self._rows: list[dict[str, Any]] = []

    def roleNames(self) -> dict[int, QByteArray]:
        return {Qt.ItemDataRole.UserRole + i: QByteArray(name.encode()) for i, name in enumerate(self._roles)}

    def rowCount(self, parent: QModelIndex | QPersistentModelIndex = QModelIndex()) -> int:  # noqa: B008
        return 0 if parent.isValid() else len(self._rows)

    def data(self, index: QModelIndex | QPersistentModelIndex, role: int = Qt.ItemDataRole.DisplayRole) -> Any:
        if not index.isValid() or not 0 <= index.row() < len(self._rows):
            return None
        offset = role - Qt.ItemDataRole.UserRole
        return self._rows[index.row()].get(self._roles[offset]) if 0 <= offset < len(self._roles) else None

    # --- for the controller ---

    @property
    def rows(self) -> list[dict[str, Any]]:
        return self._rows

    def reset(self, rows: list[dict[str, Any]]) -> None:
        self.beginResetModel()
        self._rows = list(rows)
        self.endResetModel()

    def append(self, row: dict[str, Any]) -> None:
        self.insert(len(self._rows), [row])

    def insert(self, at: int, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        self.beginInsertRows(QModelIndex(), at, at + len(rows) - 1)
        self._rows[at:at] = rows
        self.endInsertRows()

    def find(self, key: str) -> int:
        return next((i for i, row in enumerate(self._rows) if row.get("key") == key), -1)

    def update(self, index: int, **changes: Any) -> None:
        self._rows[index] = {**self._rows[index], **changes}
        model_index = self.index(index, 0)
        roles = [Qt.ItemDataRole.UserRole + self._roles.index(name) for name in changes if name in self._roles]
        self.dataChanged.emit(model_index, model_index, roles)

    def sync(self, rows: list[dict[str, Any]]) -> None:
        """Becomes `rows` with as little change as it can: the same rows at the start and at the end (by key) are
        kept and updated in place, only the middle is replaced. A view keeps its place, unlike after a reset."""
        old = self._rows
        start = 0
        while start < min(len(old), len(rows)) and old[start].get("key") == rows[start].get("key"):
            start += 1
        end_old, end_new = len(old), len(rows)
        while end_old > start and end_new > start and old[end_old - 1].get("key") == rows[end_new - 1].get("key"):
            end_old -= 1
            end_new -= 1
        if end_old > start:
            self.remove(start, end_old - start)
        self.insert(start, rows[start:end_new])
        for index, row in enumerate(rows):
            if not start <= index < end_new and self._rows[index] != row:
                self.update(index, **{k: v for k, v in row.items() if self._rows[index].get(k) != v})

    def remove(self, index: int, count: int = 1) -> None:
        if count <= 0:
            return
        self.beginRemoveRows(QModelIndex(), index, index + count - 1)
        del self._rows[index : index + count]
        self.endRemoveRows()
