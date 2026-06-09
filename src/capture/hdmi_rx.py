"""Rockchip HDMI-RX helpers (RK3588 / Rock 5T)."""
import logging
import re
import subprocess
from dataclasses import dataclass
from typing import Optional

log = logging.getLogger(__name__)


@dataclass
class HdmiRxInfo:
    width: int
    height: int
    pixel_format: str
    has_signal: bool


def _run(args: list[str], timeout: float = 3) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def prepare_hdmi_rx(device_path: str) -> Optional[HdmiRxInfo]:
    """Query DV timings and current format. Required before capture on rk_hdmirx."""
    try:
        _run(["v4l2-ctl", "-d", device_path, "--set-dv-bt-timings", "query"], timeout=4)
        timings = _run(["v4l2-ctl", "-d", device_path, "--get-dv-timings"])
        fmt = _run(["v4l2-ctl", "-d", device_path, "--get-fmt-video"])
    except (subprocess.TimeoutExpired, FileNotFoundError, subprocess.SubprocessError) as e:
        log.error("Could not query %s: %s", device_path, e)
        return None

    width = height = 0
    if timings.returncode == 0:
        for line in timings.stdout.splitlines():
            lw = re.search(r"Active width\s*:\s*(\d+)", line)
            lh = re.search(r"Active height\s*:\s*(\d+)", line)
            if lw:
                width = int(lw.group(1))
            if lh:
                height = int(lh.group(1))

    pixel_format = "NV12"
    if fmt.returncode == 0:
        match = re.search(r"Pixel Format\s*:\s*'([A-Z0-9]+)'", fmt.stdout)
        if match:
            pixel_format = match.group(1)

    has_signal = width > 0 and height > 0
    if has_signal:
        for fmt in ("NV12", "NM12", pixel_format):
            if fmt in ("BGR3", "RGB3", "AR24", "BA24"):
                continue
            set_fmt = _run([
                "v4l2-ctl", "-d", device_path,
                f"--set-fmt-video=width={width},height={height},pixelformat={fmt}",
            ])
            if set_fmt.returncode == 0:
                pixel_format = fmt
                break
        log.info("%s signal %dx%d %s", device_path, width, height, pixel_format)
    else:
        log.warning(
            "%s no active HDMI signal (plug source into HDMI IN and power it on)",
            device_path,
        )

    return HdmiRxInfo(
        width=width or 1920,
        height=height or 1080,
        pixel_format=pixel_format,
        has_signal=has_signal,
    )
