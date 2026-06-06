"""Main application window (customer-facing touchscreen)."""
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QLabel, QMessageBox, QDialog
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont

from src.ui.recording_controls import (
    RecordingPromptDialog, RecordingStatusWidget, ControlButtonsWidget
)
from src.controller.recording_controller import RecordingController


class MainWindow(QMainWindow):
    """Customer-facing touchscreen window.

    Displays recording status and Start/Stop buttons only. All configuration
    is handled through the web admin UI.
    """

    def __init__(self, controller: RecordingController):
        super().__init__()
        self.controller = controller

        self.setup_ui()

        controller.recording_started.connect(self._on_recording_started)
        controller.recording_stopped.connect(self._on_recording_stopped)
        controller.usb_inserted.connect(self._on_usb_inserted)
        controller.usb_removed.connect(self._on_usb_removed)
        controller.error_occurred.connect(self._on_error)

    def setup_ui(self):
        self.setWindowTitle("HDMI Recording Software")
        self.setMinimumSize(1024, 768)

        central = QWidget()
        self.setCentralWidget(central)
        layout = QVBoxLayout()
        layout.setSpacing(16)
        layout.setContentsMargins(32, 32, 32, 32)

        title = QLabel("HDMI Recording Software")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(28)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)

        # USB status
        self.usb_label = QLabel("No USB device connected")
        self.usb_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        usb_font = QFont()
        usb_font.setPointSize(14)
        self.usb_label.setFont(usb_font)
        layout.addWidget(self.usb_label)

        layout.addStretch()

        # Recording status
        self.status_widget = RecordingStatusWidget()
        layout.addWidget(self.status_widget)

        # Control buttons (Start / Stop — no settings button wired here)
        self.control_buttons = ControlButtonsWidget()
        self.control_buttons.start_requested.connect(self._on_start_requested)
        self.control_buttons.stop_requested.connect(self._on_stop_requested)
        # Replace settings button with admin URL notice
        self.control_buttons.settings_button.setText(
            f"Admin: http://localhost:{self.controller.web_port}"
        )
        self.control_buttons.settings_button.setEnabled(False)
        layout.addWidget(self.control_buttons)

        central.setLayout(layout)

    # ------------------------------------------------------------------ slots

    def _on_start_requested(self):
        if not self.controller.get_status()["is_recording"]:
            mount = self.controller.usb_monitor.get_first_mount_point()
            if not mount:
                QMessageBox.warning(
                    self, "No USB Device",
                    "Please insert a USB key to save recordings."
                )
                return
            dialog = RecordingPromptDialog(self, prompt_type="start")
            if dialog.exec() == QDialog.DialogCode.Accepted:
                ok, msg = self.controller.start_recording()
                if not ok:
                    QMessageBox.critical(self, "Error", f"Failed to start recording: {msg}")

    def _on_stop_requested(self):
        if self.controller.get_status()["is_recording"]:
            dialog = RecordingPromptDialog(self, prompt_type="stop")
            if dialog.exec() == QDialog.DialogCode.Accepted:
                ok, msg = self.controller.stop_recording()
                if not ok:
                    QMessageBox.critical(self, "Error", f"Failed to stop recording: {msg}")

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
            dialog = RecordingPromptDialog(self, prompt_type="start")
            if dialog.exec() == QDialog.DialogCode.Accepted:
                ok, msg = self.controller.start_recording()
                if not ok:
                    QMessageBox.critical(self, "Error", f"Failed to start recording: {msg}")

    def _on_usb_removed(self, device_info: dict):
        self.usb_label.setText("No USB device connected")
        if self.controller.get_status()["is_recording"]:
            dialog = RecordingPromptDialog(self, prompt_type="stop")
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.controller.stop_recording()

    def _on_error(self, message: str):
        QMessageBox.critical(self, "Error", message)

    def closeEvent(self, event):
        if self.controller.get_status()["is_recording"]:
            reply = QMessageBox.question(
                self, "Recording in Progress",
                "Recording is in progress. Stop and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.controller.stop_recording()
                self.controller.shutdown()
                event.accept()
            else:
                event.ignore()
        else:
            self.controller.shutdown()
            event.accept()
