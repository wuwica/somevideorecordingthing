#!/usr/bin/env python3
"""Main entry point for HDMI Recording Software."""
import sys
import os
import logging
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt, QTimer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.log import setup_logging
setup_logging()
log = logging.getLogger(__name__)

from src.controller.recording_controller import RecordingController
from src.web.server import start_web_server
from src.ui.main_window import MainWindow
from src.ui.theme import DARK_THEME
from src.ui.glib_loop import attach_glib_pump

WEB_PORT = int(os.environ.get("ADMIN_PORT", 8080))


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/default_layout.json"

    app = QApplication(sys.argv)
    app.setStyleSheet(DARK_THEME)
    if hasattr(Qt.ApplicationAttribute, "AA_EnableHighDpiScaling"):
        app.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    attach_glib_pump(app)
    controller = RecordingController(config_path, web_port=WEB_PORT)
    start_web_server(controller, host="0.0.0.0", port=WEB_PORT)
    controller.start_preview()

    window = MainWindow(controller)
    window.show()

    if os.environ.get("FULLSCREEN", "1") != "0":
        QTimer.singleShot(0, window._enter_kiosk_mode)

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
