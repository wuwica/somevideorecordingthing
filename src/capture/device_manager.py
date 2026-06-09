"""Device detection and enumeration for video and audio sources."""
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from typing import List, Optional

log = logging.getLogger(__name__)


@dataclass
class VideoDevice:
    """Represents a video capture device."""
    device_path: str
    device_id: str
    device_type: str  # 'hdmi' or 'usb'
    name: str
    capabilities: List[str]
    formats: List[str]
    driver: str = ""
    parent_label: str = ""
    gst_ready: bool = False


_SKIP_PARENT_MARKERS = (
    "video-codec", "vpu-dec", "vpu-enc", "rkvdec", "rockchip-rga",
)
_HDMI_PARENT_MARKERS = (
    "hdmirx", "hdmi_receiver", "hdmi receiver", "snps_hdmirx",
)
_HDMI_DRIVER_MARKERS = (
    "hdmirx", "rk_hdmirx", "stream_hdmirx", "snps_hdmirx", "hdmi_receiver",
)


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
        self._framerate_cache: dict = {}

    def detect_video_devices(self) -> List[VideoDevice]:
        """Detect capture-capable V4L2 devices (skips codec/metadata nodes)."""
        self.video_devices = []
        self._framerate_cache = {}  # invalidate on re-detect
        candidates: List[VideoDevice] = []
        parent_map = self._parse_v4l2_device_tree()

        for i in range(32):
            device_path = f"/dev/video{i}"
            if not os.path.exists(device_path):
                continue

            parent_label = parent_map.get(device_path, "")
            if self._is_skipped_parent(parent_label):
                continue
            if not self._is_capture_device(device_path, parent_label):
                continue

            name = self._get_device_name(device_path)
            formats = self._list_pixel_formats(device_path)
            driver = self._get_driver_name(device_path)
            device = VideoDevice(
                device_path=device_path,
                device_id=f"video{i}",
                device_type="unknown",
                name=name,
                capabilities=self._formats_to_capabilities(formats),
                formats=formats,
                driver=driver,
                parent_label=parent_label,
            )
            self._classify_device(device)
            device.gst_ready = self._gstreamer_probe(
                device_path, formats=device.formats,
            )
            candidates.append(device)

        self.video_devices = candidates

        if not self.video_devices:
            log.warning(
                "No capture devices found — "
                "check video group membership and v4l2-ctl --list-devices"
            )
        for device in self.video_devices:
            fmt_str = ", ".join(device.formats[:4]) if device.formats else "unknown"
            gst = "gst-ok" if device.gst_ready else "gst-fail"
            parent = device.parent_label or device.driver or "unknown"
            log.info(
                "%s %s – %s (%s) [%s] %s",
                device.device_type.upper(), device.device_path,
                device.name, parent, fmt_str, gst,
            )

        return self.video_devices

    def _parse_v4l2_device_tree(self) -> dict[str, str]:
        """Map /dev/videoN paths to their v4l2-ctl --list-devices parent label."""
        mapping: dict[str, str] = {}
        try:
            result = subprocess.run(
                ["v4l2-ctl", "--list-devices"],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode != 0:
                return mapping
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
            return mapping

        current_parent = ""
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("/dev/video"):
                match = re.search(r"/dev/video(\d+)", stripped)
                if match and current_parent:
                    mapping[f"/dev/video{match.group(1)}"] = current_parent
            else:
                current_parent = stripped.rstrip(":")
        return mapping

    @staticmethod
    def _is_skipped_parent(parent_label: str) -> bool:
        lower = parent_label.lower()
        return any(marker in lower for marker in _SKIP_PARENT_MARKERS)

    def _get_driver_name(self, device_path: str) -> str:
        node = os.path.basename(device_path)
        driver_link = f"/sys/class/video4linux/{node}/device/driver"
        try:
            if os.path.islink(driver_link):
                return os.path.basename(os.readlink(driver_link))
        except OSError:
            pass
        return ""

    @staticmethod
    def _is_known_capture_parent(parent_label: str) -> bool:
        if not parent_label:
            return False
        pl = parent_label.lower()
        if DeviceManager._is_skipped_parent(parent_label):
            return False
        return (
            any(marker in pl for marker in _HDMI_PARENT_MARKERS)
            or "usb" in pl
        )

    def _gstreamer_probe(
        self,
        device_path: str,
        width: int = 0,
        height: int = 0,
        formats: Optional[List[str]] = None,
    ) -> bool:
        """Test whether GStreamer v4l2src can open and pull one frame."""
        caps_variants: List[str] = []
        upper = {f.upper() for f in (formats or [])}
        if width > 0 and height > 0:
            caps_variants.append(f"video/x-raw,format=NV12,width={width},height={height}")
        if "MJPG" in upper:
            caps_variants.append("image/jpeg")
        caps_variants.append("video/x-raw,format=NV12")
        caps_variants.append("")

        probes = []
        for caps in caps_variants:
            for io_mode in ("4", "2", "0"):
                cmd = [
                    "gst-launch-1.0", "-q",
                    "v4l2src", f"device={device_path}", f"io-mode={io_mode}",
                    "num-buffers=1",
                ]
                if caps:
                    cmd.extend(["!", caps])
                cmd.extend(["!", "fakesink", "sync=false"])
                probes.append(cmd)
        for cmd in probes:
            try:
                result = subprocess.run(
                    cmd, capture_output=True, text=True, timeout=6,
                )
                if result.returncode == 0:
                    return True
            except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
                continue
        return False

    def _get_device_name(self, device_path: str) -> str:
        try:
            result = subprocess.run(
                ['v4l2-ctl', '--device', device_path, '--info'],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                name = self._extract_device_name(result.stdout)
                if name != "Unknown Device":
                    return name
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
            pass
        return self._read_sysfs_name(device_path)

    def _read_sysfs_name(self, device_path: str) -> str:
        node = os.path.basename(device_path)
        sysfs = f"/sys/class/video4linux/{node}/name"
        try:
            if os.path.exists(sysfs):
                with open(sysfs, encoding="utf-8") as f:
                    return f.read().strip() or "Unknown Device"
        except OSError:
            pass
        return "Unknown Device"

    def _is_capture_device(self, device_path: str, parent_label: str = "") -> bool:
        """Return True for nodes that can produce video frames."""
        try:
            result = subprocess.run(
                ['v4l2-ctl', '--device', device_path, '--list-formats'],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                output = result.stdout
                if "Video Capture" in output or "Video Capture Multiplanar" in output:
                    for line in output.splitlines():
                        if re.search(r'\[\d+\]:', line):
                            return True
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
            pass

        if self._is_known_capture_parent(parent_label):
            pl = parent_label.lower()
            is_hdmi = any(marker in pl for marker in _HDMI_PARENT_MARKERS)
            if is_hdmi:
                log.warning(
                    "Accepting %s from HDMI parent (%s) despite format probe failure",
                    device_path, parent_label,
                )
                return True
        return False

    def _classify_device(self, device: VideoDevice):
        parent_lower = device.parent_label.lower()
        driver_lower = device.driver.lower()
        name_lower = device.name.lower()

        if any(marker in parent_lower for marker in _HDMI_PARENT_MARKERS):
            device.device_type = "hdmi"
            return
        if any(marker in driver_lower for marker in _HDMI_DRIVER_MARKERS):
            device.device_type = "hdmi"
            return
        if "usb" in parent_lower:
            device.device_type = "usb"
            return

        hdmi_name_markers = (
            "hdmi", "hdmirx", "grabber", "frame grabber", "magewell", "elgato",
            "avermedia", "blackmagic", "game capture",
        )
        usb_name_markers = (
            "uvc", "webcam", "camera", "usb", "logitech", "microsoft",
            "hd pro", "c920", "c922", "brio", "facecam", "lrcp",
        )
        if any(marker in name_lower for marker in hdmi_name_markers):
            device.device_type = "hdmi"
        elif any(marker in name_lower for marker in usb_name_markers):
            device.device_type = "usb"
        else:
            device.device_type = "usb"

    def find_device(self, device_path: str) -> Optional[VideoDevice]:
        for device in self.video_devices:
            if device.device_path == device_path:
                return device
        return None

    def _extract_device_name(self, v4l2_output: str) -> str:
        for line in v4l2_output.split('\n'):
            if 'Card type' in line or 'Driver name' in line:
                match = re.search(r':\s*(.+)', line)
                if match:
                    return match.group(1).strip()
        return "Unknown Device"

    def _list_pixel_formats(self, device_path: str) -> List[str]:
        formats: List[str] = []
        try:
            result = subprocess.run(
                ['v4l2-ctl', '--device', device_path, '--list-formats'],
                capture_output=True,
                text=True,
                timeout=2,
            )
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    match = re.search(r"'([A-Z0-9]+)'", line)
                    if match:
                        formats.append(match.group(1))
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
            pass
        return formats

    def _formats_to_capabilities(self, formats: List[str]) -> List[str]:
        caps: List[str] = []
        upper = {f.upper() for f in formats}
        if "H264" in upper:
            caps.append("h264")
        if "MJPG" in upper:
            caps.append("mjpeg")
        if upper & {"YUYV", "NV12", "YU12", "NV21", "NM12", "BGR3", "RGB3"}:
            caps.append("yuyv")
        return caps

    def detect_audio_devices(self) -> tuple[List[AudioDevice], List[AudioDevice]]:
        """Detect audio sources and sinks using PulseAudio."""
        self.audio_sources = []
        self.audio_sinks = []

        try:
            result = subprocess.run(
                ['pactl', 'list', 'short', 'sources'],
                capture_output=True,
                text=True,
                timeout=2,
            )

            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        parts = line.split('\t')
                        if len(parts) >= 2:
                            source_name = parts[1]
                            description = parts[-1] if len(parts) > 2 else source_name
                            if '.monitor' not in source_name:
                                self.audio_sources.append(AudioDevice(
                                    name=source_name,
                                    description=description,
                                    device_type='source',
                                ))

            result = subprocess.run(
                ['pactl', 'list', 'short', 'sinks'],
                capture_output=True,
                text=True,
                timeout=2,
            )

            if result.returncode == 0:
                for line in result.stdout.strip().split('\n'):
                    if line.strip():
                        parts = line.split('\t')
                        if len(parts) >= 2:
                            sink_name = parts[1]
                            description = parts[-1] if len(parts) > 2 else sink_name
                            self.audio_sinks.append(AudioDevice(
                                name=sink_name,
                                description=description,
                                device_type='sink',
                            ))
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError) as e:
            log.warning("Could not detect audio devices: %s", e)

        return self.audio_sources, self.audio_sinks

    def _is_hdmi_hardware(self, device: VideoDevice) -> bool:
        parent_lower = device.parent_label.lower()
        driver_lower = device.driver.lower()
        return (
            any(marker in parent_lower for marker in _HDMI_PARENT_MARKERS)
            or any(marker in driver_lower for marker in _HDMI_DRIVER_MARKERS)
        )

    def get_hdmi_device(self) -> Optional[VideoDevice]:
        hdmi = [d for d in self.video_devices if d.device_type == "hdmi"]
        if not hdmi:
            return None
        hardware = [d for d in hdmi if self._is_hdmi_hardware(d)]
        for group in (hardware, hdmi):
            if not group:
                continue
            ready = [d for d in group if d.gst_ready]
            return ready[0] if ready else group[0]
        return None

    def get_usb_cameras(self) -> List[VideoDevice]:
        usb = [d for d in self.video_devices if d.device_type == "usb"]
        if not usb:
            return []

        # One logical camera can expose multiple /dev/video nodes; keep the best.
        by_parent: dict[str, List[VideoDevice]] = {}
        for device in usb:
            key = device.parent_label or device.name
            by_parent.setdefault(key, []).append(device)

        chosen: List[VideoDevice] = []
        for group in by_parent.values():
            ready = [d for d in group if d.gst_ready]
            if ready:
                chosen.append(ready[0])
                continue
            mjpg = [d for d in group if "MJPG" in {f.upper() for f in d.formats}]
            if mjpg:
                chosen.append(mjpg[0])
                continue
            chosen.append(sorted(group, key=lambda d: d.device_path)[0])

        chosen.sort(key=lambda d: d.device_path)
        return chosen

    def list_framerates(self, device_path: str) -> dict:
        """Return {format_code: [fps, ...]} for each capture format the device advertises."""
        if device_path in self._framerate_cache:
            return self._framerate_cache[device_path]
        result: dict[str, list[int]] = {}
        try:
            out = subprocess.run(
                ["v4l2-ctl", "--device", device_path, "--list-formats-ext"],
                capture_output=True, text=True, timeout=4,
            )
            if out.returncode != 0:
                return result
        except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError):
            return result

        current_fmt: str | None = None
        fps_set: set[int] = set()
        for line in out.stdout.splitlines():
            fmt_match = re.search(r"\[\d+\]:\s+'([A-Z0-9]+)'", line)
            if fmt_match:
                if current_fmt and fps_set:
                    result[current_fmt] = sorted(fps_set, reverse=True)
                current_fmt = fmt_match.group(1)
                fps_set = set()
                continue
            fps_match = re.search(r"\((\d+(?:\.\d+)?)\s*fps\)", line)
            if fps_match and current_fmt:
                fps_set.add(round(float(fps_match.group(1))))

        if current_fmt and fps_set:
            result[current_fmt] = sorted(fps_set, reverse=True)
        self._framerate_cache[device_path] = result
        return result

    def probe_device(
        self,
        device_path: str,
        width: int = 0,
        height: int = 0,
        formats: Optional[List[str]] = None,
    ) -> bool:
        return self._gstreamer_probe(device_path, width, height, formats)

    def refresh_devices(self):
        self.detect_video_devices()
        self.detect_audio_devices()
