"""USB device monitoring for USB key detection."""
import pyudev
import threading
from typing import Optional, Callable
import os


class USBMonitor:
    """Monitors USB device insertion/removal for USB keys."""
    
    def __init__(self, on_usb_inserted: Optional[Callable] = None, on_usb_removed: Optional[Callable] = None):
        """
        Initialize USB monitor.
        
        Args:
            on_usb_inserted: Callback function called when USB storage is inserted
            on_usb_removed: Callback function called when USB storage is removed
        """
        self.on_usb_inserted = on_usb_inserted
        self.on_usb_removed = on_usb_removed
        self.context = pyudev.Context()
        self.monitor = pyudev.Monitor.from_netlink(self.context)
        self.monitor.filter_by(subsystem='block')
        self.observer: Optional[pyudev.MonitorObserver] = None
        self.is_monitoring = False
        self.mounted_devices = {}
    
    def _is_usb_storage(self, device: pyudev.Device) -> bool:
        """Check if device is a USB storage device."""
        # Check if it's a disk (not a partition)
        if device.device_type != 'disk':
            return False
        
        # Check if it's removable
        if device.attributes.get('removable') != '1':
            return False
        
        # Check if it has a USB parent
        parent = device.parent
        while parent:
            if 'usb' in parent.subsystem.lower() or 'usb' in str(parent.sys_path).lower():
                return True
            parent = parent.parent
        
        return False
    
    def _get_mount_point(self, device_path: str) -> Optional[str]:
        """Get mount point for a device."""
        try:
            # Check /proc/mounts
            with open('/proc/mounts', 'r') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 2 and parts[0] == device_path:
                        return parts[1]
        except Exception:
            pass
        
        # Try to find in /media or /mnt
        device_name = os.path.basename(device_path)
        possible_paths = [
            f"/media/{os.getenv('USER', 'user')}/{device_name}",
            f"/mnt/{device_name}",
            f"/media/{device_name}",
        ]
        
        for path in possible_paths:
            if os.path.ismount(path):
                return path
        
        return None
    
    def _device_event(self, action: str, device: pyudev.Device):
        """Handle device event."""
        if not self._is_usb_storage(device):
            return
        
        device_path = device.device_node
        
        if action == 'add':
            # Wait a moment for device to be mounted
            import time
            time.sleep(0.5)
            
            mount_point = self._get_mount_point(device_path)
            if mount_point:
                device_info = {
                    'path': device_path,
                    'mount_point': mount_point,
                    'name': device.get('ID_FS_LABEL', os.path.basename(device_path)),
                    'size': device.attributes.get('size'),
                }
                self.mounted_devices[device_path] = device_info
                
                if self.on_usb_inserted:
                    self.on_usb_inserted(device_info)
        
        elif action == 'remove':
            if device_path in self.mounted_devices:
                device_info = self.mounted_devices[device_path]
                del self.mounted_devices[device_path]
                
                if self.on_usb_removed:
                    self.on_usb_removed(device_info)
    
    def start_monitoring(self):
        """Start monitoring USB devices."""
        if self.is_monitoring:
            return
        
        self.observer = pyudev.MonitorObserver(
            self.monitor,
            callback=self._device_event
        )
        self.observer.start()
        self.is_monitoring = True
        
        # Check for already connected USB devices
        self._check_existing_devices()
    
    def _check_existing_devices(self):
        """Check for USB storage devices already connected."""
        for device in self.context.list_devices(subsystem='block'):
            if self._is_usb_storage(device) and device.device_node:
                self._device_event('add', device)
    
    def stop_monitoring(self):
        """Stop monitoring USB devices."""
        if not self.is_monitoring or not self.observer:
            return
        
        self.observer.stop()
        self.is_monitoring = False
    
    def get_mounted_devices(self) -> dict:
        """Get currently mounted USB devices."""
        return self.mounted_devices.copy()
    
    def get_first_mount_point(self) -> Optional[str]:
        """Get mount point of first mounted USB device."""
        if self.mounted_devices:
            first_device = next(iter(self.mounted_devices.values()))
            return first_device.get('mount_point')
        return None

