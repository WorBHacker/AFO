"""The agent's own test window for computer control (stage 9.6, D41): a small Qt form - a name, a password, a choice,
a note, "Сохранить" and "Отправить", a long history (most of it scrolled out of sight) and a message field Enter
sends, as in a chat - that writes what happened into a file, so a test sees what the agent did.

Usage: python fake_app_window.py <log file> [title]
"""

import sys
from pathlib import Path

from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

log = Path(sys.argv[1])
app = QApplication([])
window = QWidget()
window.setWindowTitle(sys.argv[2] if len(sys.argv) > 2 else "Тестовое окно агента")
layout = QVBoxLayout(window)
layout.addWidget(QLabel("Имя"))
name = QLineEdit()
name.setAccessibleName("Имя")
layout.addWidget(name)
layout.addWidget(QLabel("Пароль"))
password = QLineEdit()
password.setAccessibleName("Пароль")
password.setEchoMode(QLineEdit.EchoMode.Password)
password.setText("секрет-123")
layout.addWidget(password)
agree = QCheckBox("Запомнить")
layout.addWidget(agree)
note = QPlainTextEdit()
note.setAccessibleName("Заметка")
layout.addWidget(note)
status = QLabel("Ничего не сохранено")
status.setAccessibleName("Состояние")
layout.addWidget(status)


def write(what: str) -> None:
    with log.open("a", encoding="utf-8") as f:
        f.write(what + "\n")


def save() -> None:
    status.setText(f"Сохранено: {name.text()}")
    write(f"save name={name.text()} remember={agree.isChecked()} note={note.toPlainText()!r}")


save_button = QPushButton("Сохранить")
save_button.clicked.connect(save)
layout.addWidget(save_button)
send_button = QPushButton("Отправить")
send_button.clicked.connect(lambda: write(f"send name={name.text()}"))
layout.addWidget(send_button)
history = QScrollArea()
history.setAccessibleName("История")
lines = QWidget()
lines_layout = QVBoxLayout(lines)
for n in range(1, 301):
    lines_layout.addWidget(QLabel(f"строка {n}"))
history.setWidget(lines)
history.setFixedHeight(90)
layout.addWidget(history)
message = QLineEdit()
message.setAccessibleName("Сообщение")


def sent() -> None:
    write(f"message={message.text()}")
    message.clear()


message.returnPressed.connect(sent)
layout.addWidget(message)
window.resize(360, 560)
window.show()
write("ready")
sys.exit(app.exec())
