"""Main application window."""
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QLabel,
    QMessageBox, QApplication
)
from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QPixmap, QImage
import gi
gi.require_version('Gst', '1.0')
gi.require_version('GstVideo', '1.0')
from gi.repository import Gst, GstVideo

from PyQt6.QtWidgets import QDialog
from src.ui.recording_controls import (
    RecordingPromptDialog, RecordingStatusWidget, ControlButtonsWidget
)
from src.ui.settings_dialog import SettingsDialog
from src.recording.recorder import Recorder
from src.usb.usb_monitor import USBMonitor
from src.capture.device_manager import DeviceManager


class MainWindow(QMainWindow):
    """Main application window."""
    
    def __init__(self, config_path: str = "config/default_layout.json"):
        super().__init__()
        self.config_path = config_path
        self.recorder: Recorder = None
        self.usb_monitor: USBMonitor = None
        self.device_manager = DeviceManager()
        self.is_recording = False
        
        Gst.init(None)
        self.setup_ui()
        self.setup_recorder()
        self.setup_usb_monitor()
        self.setup_preview()
    
    def setup_ui(self):
        """Setup main UI components."""
        self.setWindowTitle("HDMI Recording Software")
        self.setMinimumSize(1024, 768)
        
        # Central widget
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout()
        
        # Title
        title = QLabel("HDMI Recording Software")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        font = QFont()
        font.setPointSize(28)
        font.setBold(True)
        title.setFont(font)
        layout.addWidget(title)
        
        # Video preview area
        self.preview_label = QLabel("No Preview")
        self.preview_label.setMinimumHeight(400)
        self.preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.preview_label.setStyleSheet("background-color: black; color: white;")
        layout.addWidget(self.preview_label)
        
        # Status widget
        self.status_widget = RecordingStatusWidget()
        layout.addWidget(self.status_widget)
        
        # Control buttons
        self.control_buttons = ControlButtonsWidget()
        self.control_buttons.start_requested.connect(self.on_start_requested)
        self.control_buttons.stop_requested.connect(self.on_stop_requested)
        self.control_buttons.settings_requested.connect(self.on_settings_requested)
        layout.addWidget(self.control_buttons)
        
        central_widget.setLayout(layout)
    
    def setup_recorder(self):
        """Setup recorder instance."""
        try:
            self.recorder = Recorder(self.config_path)
            
            # Detect and add video sources
            self.device_manager.detect_video_devices()
            
            # Add HDMI source
            hdmi_device = self.device_manager.get_hdmi_device()
            if hdmi_device:
                self.recorder.add_video_source(hdmi_device.device_path, "hdmi")
            
            # Add USB cameras
            usb_cameras = self.device_manager.get_usb_cameras()
            for i, camera in enumerate(usb_cameras, 1):
                self.recorder.add_video_source(camera.device_path, f"camera{i}")
            
            # Setup audio
            sources, sinks = self.device_manager.detect_audio_devices()
            mic_source = sources[0].name if sources else None
            game_source = sinks[0].name if sinks else None
            self.recorder.setup_audio(mic_source, game_source)
            
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to setup recorder: {e}")
    
    def setup_usb_monitor(self):
        """Setup USB monitoring."""
        self.usb_monitor = USBMonitor(
            on_usb_inserted=self.on_usb_inserted,
            on_usb_removed=self.on_usb_removed
        )
        self.usb_monitor.start_monitoring()
    
    def setup_preview(self):
        """Setup video preview (placeholder for now)."""
        # Preview would be implemented using GStreamer video sink
        # For now, just show a placeholder
        pass
    
    def on_usb_inserted(self, device_info: dict):
        """Handle USB device insertion."""
        if not self.is_recording:
            # Prompt user to start recording
            dialog = RecordingPromptDialog(self, prompt_type="start")
            if dialog.exec() == QDialog.DialogCode.Accepted:
                mount_point = device_info.get('mount_point')
                if mount_point:
                    # Update recorder output directory
                    self.recorder.output_directory = mount_point
                self.start_recording()
    
    def on_usb_removed(self, device_info: dict):
        """Handle USB device removal."""
        if self.is_recording:
            # Prompt user to stop recording
            dialog = RecordingPromptDialog(self, prompt_type="stop")
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.stop_recording()
    
    def on_start_requested(self):
        """Handle start recording request."""
        if not self.is_recording:
            # Check if USB is mounted
            mount_point = self.usb_monitor.get_first_mount_point()
            if mount_point:
                self.recorder.output_directory = mount_point
                dialog = RecordingPromptDialog(self, prompt_type="start")
                if dialog.exec() == QDialog.DialogCode.Accepted:
                    self.start_recording()
            else:
                QMessageBox.warning(
                    self,
                    "No USB Device",
                    "Please insert a USB key to save recordings."
                )
    
    def on_stop_requested(self):
        """Handle stop recording request."""
        if self.is_recording:
            dialog = RecordingPromptDialog(self, prompt_type="stop")
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self.stop_recording()
    
    def on_settings_requested(self):
        """Handle settings request."""
        dialog = SettingsDialog(self.config_path, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            # Reload recorder configuration
            if self.recorder:
                self.recorder.layout_engine.reload_config()
                self.recorder._setup_from_config()
    
    def start_recording(self):
        """Start recording."""
        try:
            self.recorder.start_recording()
            self.is_recording = True
            self.status_widget.set_recording(True)
            self.control_buttons.set_recording_state(True)
            
            QMessageBox.information(
                self,
                "Recording Started",
                f"Recording started. Output: {self.recorder.get_output_path()}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to start recording: {e}")
            self.is_recording = False
            self.status_widget.set_recording(False)
            self.control_buttons.set_recording_state(False)
    
    def stop_recording(self):
        """Stop recording."""
        try:
            output_path = self.recorder.get_output_path()
            self.recorder.stop_recording()
            self.is_recording = False
            self.status_widget.set_recording(False)
            self.control_buttons.set_recording_state(False)
            
            QMessageBox.information(
                self,
                "Recording Stopped",
                f"Recording saved to: {output_path}"
            )
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to stop recording: {e}")
    
    def closeEvent(self, event):
        """Handle window close event."""
        if self.is_recording:
            reply = QMessageBox.question(
                self,
                "Recording in Progress",
                "Recording is in progress. Do you want to stop and exit?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
            )
            if reply == QMessageBox.StandardButton.Yes:
                self.stop_recording()
                event.accept()
            else:
                event.ignore()
        else:
            if self.usb_monitor:
                self.usb_monitor.stop_monitoring()
            event.accept()

