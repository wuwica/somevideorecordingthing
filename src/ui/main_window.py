"""Main application window (customer-facing touchscreen)."""
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel, QMessageBox,
    QApplication, QSizePolicy, QPushButton,
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QFont, QPixmap, QImage

from src.ui.recording_controls import RecordingStatusWidget, ControlButtonsWidget
from src.ui.inline_widgets import InlinePrompt
from src.controller.recording_controller import RecordingController


class NotificationBar(QWidget):
    """Dismissable in-app notification bar shown at the top of the window."""

    def __init__(self, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout()
        layout.setContentsMargins(12, 6, 12, 6)

        self.message_label = QLabel()
        self.message_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        msg_font = QFont()
        msg_font.setPointSize(14)
        self.message_label.setFont(msg_font)
        layout.addWidget(self.message_label, stretch=1)

        close_btn = QPushButton("×")
        close_btn.setFixedSize(32, 32)
        close_btn.setStyleSheet(
            "QPushButton { font-size: 18px; border: none; background: transparent; color: white; }"
        )
        close_btn.clicked.connect(self.hide)
        layout.addWidget(close_btn)

        self.setLayout(layout)
        self.setVisible(False)

        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self.hide)

    def show_message(self, message: str, level: str = "error"):
        self.message_label.setText(message)
        if level == "warning":
            self.setStyleSheet("background-color: #e67e00; color: white;")
        else:
            self.setStyleSheet("background-color: #c0392b; color: white;")
        self.setVisible(True)
        self._timer.start(5000)


class MainWindow(QMainWindow):
    """Customer-facing touchscreen window."""

    def __init__(self, controller: RecordingController):
        super().__init__()
        self.controller = controller
        self._prompt_action = None

        self.setup_ui()

        controller.recording_started.connect(self._on_recording_started)
        controller.recording_stopped.connect(self._on_recording_stopped)
        controller.usb_inserted.connect(self._on_usb_inserted)
        controller.usb_removed.connect(self._on_usb_removed)
        controller.error_occurred.connect(self._on_error)
        controller.preview_frame_ready.connect(self._on_preview_frame)

    def setup_ui(self):
        self.setWindowTitle("HDMI Recording Software")

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout()
        layout.setSpacing(16)
        layout.setContentsMargins(24, 24, 24, 24)

        screen_w = QApplication.primaryScreen().size().width() if QApplication.primaryScreen() else 1280
        title_pt = max(16, min(28, screen_w // 45))
        usb_pt   = max(10, min(14, screen_w // 80))

        title = QLabel("YUMEREC")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(title_pt)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        # In-app notification bar (hidden until needed)
        self.notification_bar = NotificationBar()
        layout.addWidget(self.notification_bar)

        # USB status
        self.usb_label = QLabel("No USB device connected")
        self.usb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        usb_font = QFont()
        usb_font.setPointSize(usb_pt)
        self.usb_label.setFont(usb_font)
        layout.addWidget(self.usb_label)

        # Preview — expands to fill all available space
        self.preview_label = QLabel()
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("background: #111;")
        self.preview_label.setSizePolicy(
            QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding
        )
        layout.addWidget(self.preview_label, stretch=1)

        # Recording status
        self.status_widget = RecordingStatusWidget()
        layout.addWidget(self.status_widget)

        # Control buttons (Start / Stop)
        self.control_buttons = ControlButtonsWidget()
        self.control_buttons.start_requested.connect(self._on_start_requested)
        self.control_buttons.stop_requested.connect(self._on_stop_requested)
        layout.addWidget(self.control_buttons)

        # Inline yes/no prompt (replaces modal dialogs)
        self.prompt = InlinePrompt()
        self.prompt.confirmed.connect(self._on_prompt_confirmed)
        self.prompt.cancelled.connect(self._on_prompt_cancelled)
        layout.addWidget(self.prompt)

        central.setLayout(layout)

    # ------------------------------------------------------------------ notifications

    def show_notification(self, message: str, level: str = "error"):
        self.notification_bar.show_message(message, level)

    # ------------------------------------------------------------------ inline prompts

    def _show_prompt(self, text: str, action: str):
        self._prompt_action = action
        self.prompt.show_prompt(text)

    def _on_prompt_confirmed(self):
        action = self._prompt_action
        self._prompt_action = None

        if action == "start":
            ok, msg = self.controller.start_recording()
            if not ok:
                self.show_notification(f"Failed to start recording: {msg}")
        elif action == "stop":
            ok, msg = self.controller.stop_recording()
            if not ok:
                self.show_notification(f"Failed to stop recording: {msg}")
        elif action == "exit":
            self.controller.stop_recording()
            self.controller.shutdown()
            self.close()

    def _on_prompt_cancelled(self):
        self._prompt_action = None

    # ------------------------------------------------------------------ slots

    def _on_start_requested(self):
        if not self.controller.get_status()["is_recording"]:
            mount = self.controller.usb_monitor.get_first_mount_point()
            if not mount:
                self.show_notification(
                    "Please insert a USB key to save recordings.", level="warning"
                )
                return
            self._show_prompt("Start Recording?", "start")

    def _on_stop_requested(self):
        if self.controller.get_status()["is_recording"]:
            self._show_prompt("Stop Recording?", "stop")

    def _on_recording_started(self, output_path: str):
        self.status_widget.set_recording(True)
        self.control_buttons.set_recording_state(True)

    def _on_recording_stopped(self, output_path: str):
        self.status_widget.set_recording(False)
        self.control_buttons.set_recording_state(False)

    def _on_usb_inserted(self, device_info: dict):
        name = device_info.get("name", device_info.get("path", "USB device"))
        self.usb_label.setText(f"USB: {name} — {device_info.get('mount_point', '')}")
        if not self.controller.get_status()["is_recording"]:
            self._show_prompt("USB inserted. Start Recording?", "start")

    def _on_usb_removed(self, device_info: dict):
        self.usb_label.setText("No USB device connected")
        if self.controller.get_status()["is_recording"]:
            self._show_prompt("USB removed. Stop Recording?", "stop")

    def _enter_kiosk_mode(self):
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.showFullScreen()

    def _on_preview_frame(self):
        image = self.controller.get_preview_image()
        if image and not image.isNull():
            pix = QPixmap.fromImage(image)
            lw, lh = self.preview_label.width(), self.preview_label.height()
            if lw > 10 and lh > 10:
                pix = pix.scaled(
                    lw, lh,
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
            self.preview_label.setPixmap(pix)

    def _on_error(self, message: str):
        self.show_notification(message)

    def closeEvent(self, event):
        if self.controller.get_status()["is_recording"]:
            self._show_prompt("Recording in progress. Stop and exit?", "exit")
            event.ignore()
        else:
            self.controller.shutdown()
            event.accept()
