#!/usr/bin/env python3
"""Main entry point for HDMI Recording Software."""
import sys
import os
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt

# Add src to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from src.ui.main_window import MainWindow


def main():
    """Main function."""
    app = QApplication(sys.argv)
    app.setAttribute(Qt.ApplicationAttribute.AA_EnableHighDpiScaling, True)
    
    # Get config path from command line or use default
    config_path = sys.argv[1] if len(sys.argv) > 1 else "config/default_layout.json"
    
    window = MainWindow(config_path)
    window.show()
    
    sys.exit(app.exec())


if __name__ == "__main__":
    main()

