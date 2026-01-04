"""Recording control UI components."""
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QLabel,
    QDialog, QDialogButtonBox, QMessageBox
)
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QFont


class RecordingPromptDialog(QDialog):
    """Dialog for prompting user to start/stop recording."""
    
    def __init__(self, parent=None, prompt_type: str = "start"):
        """
        Initialize prompt dialog.
        
        Args:
            parent: Parent widget
            prompt_type: "start" or "stop"
        """
        super().__init__(parent)
        self.prompt_type = prompt_type
        self.setup_ui()
    
    def setup_ui(self):
        """Setup UI components."""
        self.setWindowTitle("Recording Control")
        self.setMinimumSize(400, 200)
        
        layout = QVBoxLayout()
        
        # Large label for prompt
        label_text = "Start Recording?" if self.prompt_type == "start" else "Stop Recording?"
        label = QLabel(label_text)
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(24)
        font.setBold(True)
        label.setFont(font)
        layout.addWidget(label)
        
        # Buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Yes | QDialogButtonBox.StandardButton.No
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)
        
        self.setLayout(layout)
        
        # Make buttons large for touch
        for button in button_box.buttons():
            button.setMinimumHeight(60)
            button.setMinimumWidth(150)


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
    settings_requested = pyqtSignal()
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setup_ui()
    
    def setup_ui(self):
        """Setup UI components."""
        layout = QVBoxLayout()
        
        # Start button
        self.start_button = QPushButton("Start Recording")
        self.start_button.setMinimumHeight(80)
        self.start_button.setStyleSheet("""
            QPushButton {
                background-color: #4CAF50;
                color: white;
                font-size: 20px;
                font-weight: bold;
                border-radius: 10px;
            }
            QPushButton:pressed {
                background-color: #45a049;
            }
        """)
        self.start_button.clicked.connect(self.start_requested.emit)
        layout.addWidget(self.start_button)
        
        # Stop button
        self.stop_button = QPushButton("Stop Recording")
        self.stop_button.setMinimumHeight(80)
        self.stop_button.setEnabled(False)
        self.stop_button.setStyleSheet("""
            QPushButton {
                background-color: #f44336;
                color: white;
                font-size: 20px;
                font-weight: bold;
                border-radius: 10px;
            }
            QPushButton:pressed {
                background-color: #da190b;
            }
            QPushButton:disabled {
                background-color: #cccccc;
                color: #666666;
            }
        """)
        self.stop_button.clicked.connect(self.stop_requested.emit)
        layout.addWidget(self.stop_button)
        
        # Settings button
        self.settings_button = QPushButton("Settings")
        self.settings_button.setMinimumHeight(60)
        self.settings_button.clicked.connect(self.settings_requested.emit)
        layout.addWidget(self.settings_button)
        
        self.setLayout(layout)
    
    def set_recording_state(self, is_recording: bool):
        """Update button states based on recording status."""
        self.start_button.setEnabled(not is_recording)
        self.stop_button.setEnabled(is_recording)

