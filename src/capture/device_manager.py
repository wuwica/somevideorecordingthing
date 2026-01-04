"""Device detection and enumeration for video and audio sources."""
import os
import subprocess
import re
from typing import List, Dict, Optional
from dataclasses import dataclass


@dataclass
class VideoDevice:
    """Represents a video capture device."""
    device_path: str
    device_id: str
    device_type: str  # 'hdmi' or 'usb'
    name: str
    capabilities: List[str]


@dataclass
class AudioDevice:
    """Represents an audio source/sink."""
    name: str
    description: str
    device_type: str  # 'source' or 'sink'


class DeviceManager:
    """Manages detection and enumeration of video and audio devices."""
    
    def __init__(self):
        self.video_devices: List[VideoDevice] = []
        self.audio_sources: List[AudioDevice] = []
        self.audio_sinks: List[AudioDevice] = []
    
    def detect_video_devices(self) -> List[VideoDevice]:
        """Detect and enumerate all video capture devices."""
        self.video_devices = []
        
        # Check /dev/video* devices
        video_devices = []
        for i in range(32):  # Check up to /dev/video31
            device_path = f"/dev/video{i}"
            if os.path.exists(device_path):
                try:
                    # Use v4l2-ctl to get device info
                    result = subprocess.run(
                        ['v4l2-ctl', '--device', device_path, '--info'],
                        capture_output=True,
                        text=True,
                        timeout=2
                    )
                    
                    if result.returncode == 0:
                        name = self._extract_device_name(result.stdout)
                        device_type = self._determine_device_type(device_path, name)
                        
                        device = VideoDevice(
                            device_path=device_path,
                            device_id=f"video{i}",
                            device_type=device_type,
                            name=name,
                            capabilities=self._get_device_capabilities(device_path)
                        )
                        video_devices.append(device)
                except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
                    continue
        
        self.video_devices = video_devices
        return self.video_devices
    
    def _extract_device_name(self, v4l2_output: str) -> str:
        """Extract device name from v4l2-ctl output."""
        for line in v4l2_output.split('\n'):
            if 'Card type' in line or 'Driver name' in line:
                # Try to extract meaningful name
                match = re.search(r':\s*(.+)', line)
                if match:
                    return match.group(1).strip()
        return "Unknown Device"
    
    def _determine_device_type(self, device_path: str, name: str) -> str:
        """Determine if device is HDMI capture or USB camera."""
        name_lower = name.lower()
        
        # Check for HDMI capture indicators
        hdmi_indicators = ['hdmi', 'capture', 'grabber', 'frame grabber']
        if any(indicator in name_lower for indicator in hdmi_indicators):
            return 'hdmi'
        
        # Check device capabilities or use heuristics
        # USB cameras often have 'USB' in the name or are typically /dev/video1+
        if 'usb' in name_lower:
            return 'usb'
        
        # Default: assume first device is HDMI, others are USB
        if device_path == '/dev/video0':
            return 'hdmi'
        else:
            return 'usb'
    
    def _get_device_capabilities(self, device_path: str) -> List[str]:
        """Get device capabilities."""
        capabilities = []
        try:
            result = subprocess.run(
                ['v4l2-ctl', '--device', device_path, '--list-formats'],
                capture_output=True,
                text=True,
                timeout=2
            )
            if result.returncode == 0:
                if 'H264' in result.stdout or 'h264' in result.stdout:
                    capabilities.append('h264')
                if 'MJPG' in result.stdout or 'mjpeg' in result.stdout:
                    capabilities.append('mjpeg')
                if 'YUYV' in result.stdout or 'yuyv' in result.stdout:
                    capabilities.append('yuyv')
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
            pass
        return capabilities
    
    def detect_audio_devices(self) -> tuple[List[AudioDevice], List[AudioDevice]]:
        """Detect audio sources and sinks using PulseAudio."""
        self.audio_sources = []
        self.audio_sinks = []
        
        try:
            # Get list of sources (microphones)
            result = subprocess.run(
                ['pactl', 'list', 'short', 'sources'],
                capture_output=True,
                text=True,
                timeout=2
            )
            
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        parts = line.split('\t')
                        if len(parts) >= 2:
                            source_name = parts[1]
                            description = parts[-1] if len(parts) > 2 else source_name
                            
                            # Skip monitor sources (they're for capturing output)
                            if '.monitor' not in source_name:
                                device = AudioDevice(
                                    name=source_name,
                                    description=description,
                                    device_type='source'
                                )
                                self.audio_sources.append(device)
            
            # Get list of sinks (for game audio capture)
            result = subprocess.run(
                ['pactl', 'list', 'short', 'sinks'],
                capture_output=True,
                text=True,
                timeout=2
            )
            
            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        parts = line.split('\t')
                        if len(parts) >= 2:
                            sink_name = parts[1]
                            description = parts[-1] if len(parts) > 2 else sink_name
                            
                            device = AudioDevice(
                                name=sink_name,
                                description=description,
                                device_type='sink'
                            )
                            self.audio_sinks.append(device)
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError) as e:
            print(f"Warning: Could not detect audio devices: {e}")
        
        return self.audio_sources, self.audio_sinks
    
    def get_hdmi_device(self) -> Optional[VideoDevice]:
        """Get the primary HDMI capture device."""
        for device in self.video_devices:
            if device.device_type == 'hdmi':
                return device
        return None
    
    def get_usb_cameras(self) -> List[VideoDevice]:
        """Get all USB camera devices."""
        return [device for device in self.video_devices if device.device_type == 'usb']
    
    def refresh_devices(self):
        """Refresh all device lists."""
        self.detect_video_devices()
        self.detect_audio_devices()

