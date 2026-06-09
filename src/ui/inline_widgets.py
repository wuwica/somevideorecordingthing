"""In-window banners and prompts (no modal dialogs)."""
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt, pyqtSignal, QTimer
from PyQt6.QtGui import QFont


class InlineBanner(QWidget):
    """Non-blocking message bar shown at the top of the main window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVisible(False)
        layout = QHBoxLayout()
        layout.setContentsMargins(16, 10, 16, 10)

        self._label = QLabel()
        self._label.setWordWrap(True)
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(14)
        self._label.setFont(font)
        layout.addWidget(self._label)

        dismiss = QPushButton("✕")
        dismiss.setFixedSize(36, 36)
        dismiss.clicked.connect(self.hide)
        layout.addWidget(dismiss)

        self.setLayout(layout)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, text: str, level: str = "info", auto_dismiss_ms: int = 8000):
        styles = {
            "error": "background-color: #5c1a1a; color: #fecaca; border: 2px solid #dc2626;",
            "warning": "background-color: #5c4a1a; color: #fef08a; border: 2px solid #ca8a04;",
            "info": "background-color: #1a3a5c; color: #bfdbfe; border: 2px solid #2563eb;",
        }
        self.setStyleSheet(styles.get(level, styles["info"]))
        self._label.setText(text)
        self.setVisible(True)
        self._timer.stop()
        if auto_dismiss_ms > 0:
            self._timer.start(auto_dismiss_ms)


class InlinePrompt(QWidget):
    """Yes/No confirmation panel embedded in the main window."""

    confirmed = pyqtSignal()
    cancelled = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setVisible(False)
        self.setStyleSheet(
            "background-color: rgba(15, 17, 23, 0.95);"
            "border: 2px solid #334155; border-radius: 12px;"
        )

        layout = QVBoxLayout()
        layout.setContentsMargins(24, 20, 24, 20)
        layout.setSpacing(16)

        self._label = QLabel()
        self._label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(22)
        font.setBold(True)
        self._label.setFont(font)
        self._label.setStyleSheet("color: #f1f5f9;")
        layout.addWidget(self._label)

        btn_row = QHBoxLayout()
        btn_row.setSpacing(16)

        self._yes_btn = QPushButton("Yes")
        self._yes_btn.setMinimumHeight(64)
        self._yes_btn.setMinimumWidth(160)
        self._yes_btn.setStyleSheet(
            "background-color: #16a34a; color: white; font-size: 18px;"
            "font-weight: bold; border-radius: 8px;"
        )
        self._yes_btn.clicked.connect(self._on_yes)

        self._no_btn = QPushButton("No")
        self._no_btn.setMinimumHeight(64)
        self._no_btn.setMinimumWidth(160)
        self._no_btn.setStyleSheet(
            "background-color: #475569; color: white; font-size: 18px;"
            "font-weight: bold; border-radius: 8px;"
        )
        self._no_btn.clicked.connect(self._on_no)

        btn_row.addStretch()
        btn_row.addWidget(self._yes_btn)
        btn_row.addWidget(self._no_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        self.setLayout(layout)

    def show_prompt(self, text: str):
        self._label.setText(text)
        self.setVisible(True)

    def hide_prompt(self):
        self.setVisible(False)

    def _on_yes(self):
        self.hide_prompt()
        self.confirmed.emit()

    def _on_no(self):
        self.hide_prompt()
        self.cancelled.emit()
