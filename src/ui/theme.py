"""Dark theme stylesheet for the touchscreen app."""

DARK_THEME = """
QMainWindow, QWidget {
    background-color: #0f1117;
    color: #e2e8f0;
    font-family: system-ui, sans-serif;
}

QLabel {
    color: #e2e8f0;
    background: transparent;
}

QPushButton {
    background-color: #1e293b;
    color: #e2e8f0;
    border: 1px solid #334155;
    border-radius: 8px;
    padding: 8px 16px;
    font-weight: 600;
}

QPushButton:pressed {
    background-color: #334155;
}

QPushButton:disabled {
    background-color: #1a1d27;
    color: #64748b;
    border-color: #2d3148;
}
"""
