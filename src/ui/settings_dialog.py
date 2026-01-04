"""Settings dialog for configuration."""
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QComboBox, QCheckBox, QFileDialog, QMessageBox, QGroupBox
)
from PyQt6.QtCore import Qt
import json
import os


class SettingsDialog(QDialog):
    """Settings dialog for configuring recording options."""
    
    def __init__(self, config_path: str, parent=None):
        """
        Initialize settings dialog.
        
        Args:
            config_path: Path to configuration file
            parent: Parent widget
        """
        super().__init__(parent)
        self.config_path = config_path
        self.config = {}
        self.load_config()
        self.setup_ui()
    
    def load_config(self):
        """Load configuration from file."""
        if os.path.exists(self.config_path):
            with open(self.config_path, 'r') as f:
                self.config = json.load(f)
        else:
            self.config = {}
    
    def save_config(self):
        """Save configuration to file."""
        os.makedirs(os.path.dirname(self.config_path), exist_ok=True)
        with open(self.config_path, 'w') as f:
            json.dump(self.config, f, indent=2)
    
    def setup_ui(self):
        """Setup UI components."""
        self.setWindowTitle("Settings")
        self.setMinimumSize(600, 500)
        
        layout = QVBoxLayout()
        
        # Audio settings
        audio_group = QGroupBox("Audio Settings")
        audio_layout = QVBoxLayout()
        
        self.mic_enabled = QCheckBox("Enable Microphone")
        self.mic_enabled.setChecked(self.config.get("audio", {}).get("mic_enabled", True))
        audio_layout.addWidget(self.mic_enabled)
        
        self.game_audio_enabled = QCheckBox("Enable Game Audio")
        self.game_audio_enabled.setChecked(self.config.get("audio", {}).get("game_audio_enabled", True))
        audio_layout.addWidget(self.game_audio_enabled)
        
        audio_group.setLayout(audio_layout)
        layout.addWidget(audio_group)
        
        # Stop frame settings
        stop_frame_group = QGroupBox("Auto-Stop Frame")
        stop_frame_layout = QVBoxLayout()
        
        self.stop_frame_enabled = QCheckBox("Enable Auto-Stop Frame Detection")
        stop_frame_config = self.config.get("stop_frame", {})
        self.stop_frame_enabled.setChecked(stop_frame_config.get("enabled", False))
        stop_frame_layout.addWidget(self.stop_frame_enabled)
        
        # Stop frame image selection
        frame_layout = QHBoxLayout()
        frame_layout.addWidget(QLabel("Stop Frame Image:"))
        self.frame_path_label = QLabel(stop_frame_config.get("image", "Not set"))
        frame_layout.addWidget(self.frame_path_label)
        self.frame_browse_button = QPushButton("Browse...")
        self.frame_browse_button.clicked.connect(self.browse_stop_frame)
        frame_layout.addWidget(self.frame_browse_button)
        stop_frame_layout.addLayout(frame_layout)
        
        stop_frame_group.setLayout(stop_frame_layout)
        layout.addWidget(stop_frame_group)
        
        # Buttons
        button_layout = QHBoxLayout()
        save_button = QPushButton("Save")
        save_button.setMinimumHeight(60)
        save_button.clicked.connect(self.save_settings)
        button_layout.addWidget(save_button)
        
        cancel_button = QPushButton("Cancel")
        cancel_button.setMinimumHeight(60)
        cancel_button.clicked.connect(self.reject)
        button_layout.addWidget(cancel_button)
        
        layout.addLayout(button_layout)
        self.setLayout(layout)
    
    def browse_stop_frame(self):
        """Browse for stop frame image."""
        file_path, _ = QFileDialog.getOpenFileName(
            self,
            "Select Stop Frame Image",
            "",
            "Image Files (*.png *.jpg *.jpeg *.bmp)"
        )
        if file_path:
            self.frame_path_label.setText(file_path)
    
    def save_settings(self):
        """Save settings to configuration file."""
        # Update audio settings
        if "audio" not in self.config:
            self.config["audio"] = {}
        self.config["audio"]["mic_enabled"] = self.mic_enabled.isChecked()
        self.config["audio"]["game_audio_enabled"] = self.game_audio_enabled.isChecked()
        
        # Update stop frame settings
        if "stop_frame" not in self.config:
            self.config["stop_frame"] = {}
        self.config["stop_frame"]["enabled"] = self.stop_frame_enabled.isChecked()
        frame_path = self.frame_path_label.text()
        if frame_path != "Not set":
            self.config["stop_frame"]["image"] = frame_path
        
        try:
            self.save_config()
            QMessageBox.information(self, "Settings", "Settings saved successfully!")
            self.accept()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to save settings: {e}")

