"""USB device monitoring for USB key detection."""
import logging
import os
import threading
from typing import Optional, Callable

log = logging.getLogger(__name__)

try:
    import pyudev as _pyudev
    _PYUDEV_OK = True
except ImportError:
    _PYUDEV_OK = False
    log.warning("pyudev not installed — USB storage monitoring disabled")


class USBMonitor:
    """Monitors USB device insertion/removal for USB keys.

    Gracefully degrades when pyudev is unavailable (ImportError) or when
    the udev netlink socket can't be created (WSL2, containers, etc.).
    In those cases start_monitoring() is a no-op and no callbacks are fired.
    """

    def __init__(
        self,
        on_usb_inserted: Optional[Callable] = None,
        on_usb_removed: Optional[Callable] = None,
    ):
        self.on_usb_inserted = on_usb_inserted
        self.on_usb_removed = on_usb_removed
        self.is_monitoring = False
        self.mounted_devices: dict = {}
        self._available = False
        self._observer = None
        self._context = None
        self._monitor = None

        if not _PYUDEV_OK:
            return
        try:
            self._context = _pyudev.Context()
            self._monitor = _pyudev.Monitor.from_netlink(self._context)
            self._monitor.filter_by(subsystem='block')
            self._available = True
        except Exception as e:
            log.warning("USB monitoring unavailable (%s) — pyudev netlink failed", e)

    # ------------------------------------------------------------------

    def _is_usb_storage(self, device) -> bool:
        dev_type = device.device_type
        # Accept both whole disks and partitions. For partitions, check the
        # parent disk for the removable flag; the partition itself may not have it.
        if dev_type == 'partition':
            disk = device.parent
            if disk is None or disk.device_type != 'disk':
                return False
            check = disk
        elif dev_type == 'disk':
            check = device
        else:
            return False
        if check.get('ID_BUS') == 'usb':
            return True
        ancestor = check.parent
        while ancestor:
            if 'usb' in (ancestor.subsystem or '').lower():
                return True
            ancestor = ancestor.parent
        log.debug("USB: %s rejected (ID_BUS=%r)", device.device_node, check.get('ID_BUS'))
        return False

    def _get_mount_point(self, device_path: str) -> Optional[str]:
        # Match exact device OR any partition of that disk (e.g. /dev/sda -> /dev/sda1)
        try:
            with open('/proc/mounts') as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 2:
                        continue
                    dev = parts[0]
                    if dev == device_path or dev.startswith(device_path):
                        return parts[1]
        except Exception:
            pass
        # Fallback: scan common automount dirs (run/media for systemd, media for older)
        user = os.getenv('USER', '')
        for base in (f"/run/media/{user}", "/run/media", f"/media/{user}", "/media", "/mnt"):
            try:
                for entry in os.scandir(base):
                    if entry.is_dir() and os.path.ismount(entry.path):
                        try:
                            with open('/proc/mounts') as f:
                                for line in f:
                                    parts = line.split()
                                    if len(parts) >= 2 and parts[1] == entry.path:
                                        dev = parts[0]
                                        if dev == device_path or dev.startswith(device_path):
                                            return entry.path
                        except Exception:
                            pass
            except Exception:
                pass
        return None

    def _device_event(self, action: str, hotplug: bool, device) -> None:
        if not self._is_usb_storage(device):
            log.debug("USB: skipping %s (not USB storage)", device.device_node)
            return
        device_path = device.device_node
        log.debug("USB: %s event for %s", action, device_path)
        if action == 'add':
            if hotplug:
                # Retry up to 3s — automount may lag behind the udev event
                import time
                mount_point = None
                for _ in range(6):
                    time.sleep(0.5)
                    mount_point = self._get_mount_point(device_path)
                    if mount_point:
                        break
            else:
                mount_point = self._get_mount_point(device_path)
            if mount_point:
                log.info("USB inserted: %s → %s", device_path, mount_point)
                info = {
                    'path': device_path,
                    'mount_point': mount_point,
                    'name': device.get('ID_FS_LABEL') or os.path.basename(device_path),
                    'size': device.attributes.get('size'),
                }
                self.mounted_devices[device_path] = info
                if self.on_usb_inserted:
                    self.on_usb_inserted(info)
            else:
                log.warning("USB: %s detected but no mount point found", device_path)
        elif action == 'remove':
            info = self.mounted_devices.pop(device_path, None)
            if info and self.on_usb_removed:
                self.on_usb_removed(info)

    def _check_existing_devices(self) -> None:
        if not self._context:
            return
        try:
            for device in self._context.list_devices(subsystem='block'):
                if self._is_usb_storage(device) and device.device_node:
                    self._device_event('add', hotplug=False, device=device)
        except Exception as e:
            log.warning("Error scanning existing USB devices: %s", e)

    # ------------------------------------------------------------------

    def _hotplug_event(self, *args) -> None:
        # pyudev < 0.21 calls callback(action, device); >= 0.21 calls callback(device)
        # with device.action holding the string. Handle both.
        if len(args) == 2 and isinstance(args[0], str):
            action, device = args
        else:
            device = args[0]
            action = device.action
        self._device_event(action, hotplug=True, device=device)

    def start_monitoring(self) -> None:
        if not self._available or self.is_monitoring:
            return
        try:
            self._observer = _pyudev.MonitorObserver(
                self._monitor, callback=self._hotplug_event
            )
            self._observer.start()
            self.is_monitoring = True
            self._check_existing_devices()
            log.info("USB storage monitoring started")
        except Exception as e:
            log.warning("Failed to start USB monitor: %s", e)

    def stop_monitoring(self) -> None:
        if not self.is_monitoring or not self._observer:
            return
        try:
            self._observer.stop()
        except Exception:
            pass
        self.is_monitoring = False

    def get_mounted_devices(self) -> dict:
        return self.mounted_devices.copy()

    def get_first_mount_point(self) -> Optional[str]:
        if self.mounted_devices:
            return next(iter(self.mounted_devices.values())).get('mount_point')
        return None
