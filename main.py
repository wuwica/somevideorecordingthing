#!/usr/bin/env python3
"""Main entry point for HDMI Recording Software."""
import sys
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.controller.recording_controller import RecordingController
from src.web.server import start_web_server
from src.ui.main_window import MainWindow

WEB_PORT = int(os.environ.get("ADMIN_PORT", 8080))


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/default_layout.json"

    app = QApplication(sys.argv)
    app.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)

    controller = RecordingController(config_path, web_port=WEB_PORT)
    start_web_server(controller, host="0.0.0.0", port=WEB_PORT)

    window = MainWindow(controller)
    window.show()

    sys.exit(app.exec())


if __name__ == "__main__":
    main()
