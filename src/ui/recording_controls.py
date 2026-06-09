"""Recording control UI components."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QSizePolicy,
)
from PyQt6.QtCore import pyqtSignal
from PyQt6.QtGui import QFont


class RecordingStatusWidget(QWidget):
    """Widget displaying recording status."""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
    def setup_ui(self):
        """Setup UI components."""
        layout = QHBoxLayout()
        
        # Status indicator (red dot)
        self.status_label = QLabel("●")
        self.status_label.setStyleSheet("color: gray; font-size: 24px;")
        layout.addWidget(self.status_label)
        
        # Status text
        self.status_text = QLabel("Not Recording")
        font = QFont()
        font.setPointSize(16)
        self.status_text.setFont(font)
        layout.addWidget(self.status_text)
        
        layout.addStretch()
        self.setLayout(layout)
    
    def set_recording(self, is_recording: bool):
        """Update recording status display."""
        if is_recording:
            self.status_label.setStyleSheet("color: red; font-size: 24px;")
            self.status_text.setText("Recording...")
        else:
            self.status_label.setStyleSheet("color: gray; font-size: 24px;")
            self.status_text.setText("Not Recording")


class ControlButtonsWidget(QWidget):
    """Widget with control buttons."""
    
    start_requested = pyqtSignal()
    stop_requested = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
    def setup_ui(self):
        """Setup UI components."""
        outer = QVBoxLayout()
        outer.setSpacing(12)

        def _centered_row(btn):
            row = QHBoxLayout()
            row.addWidget(btn)
            return row

        # Start button
        self.start_button = QPushButton("Start Recording")
        self.start_button.setMinimumHeight(80)
        self.start_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.start_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-size: 20px;
                font-weight: bold;
                border-radius: 10px;
                padding: 0 24px;
            }
            QPushButton:pressed { background-color: #45a049; }
        """)
        self.start_button.clicked.connect(self.start_requested.emit)
        outer.addLayout(_centered_row(self.start_button))

        # Stop button
        self.stop_button = QPushButton("Stop Recording")
        self.stop_button.setMinimumHeight(80)
        self.stop_button.setEnabled(False)
        self.stop_button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.stop_button.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                font-size: 20px;
                font-weight: bold;
                border-radius: 10px;
                padding: 0 24px;
            }
            QPushButton:pressed   { background-color: #da190b; }
            QPushButton:disabled  { background-color: #555; color: #999; }
        """)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        outer.addLayout(_centered_row(self.stop_button))

        self.setLayout(outer)
    
    def set_recording_state(self, is_recording: bool):
        """Update button states based on recording status."""
        self.start_button.setEnabled(not is_recording)
        self.stop_button.setEnabled(is_recording)

