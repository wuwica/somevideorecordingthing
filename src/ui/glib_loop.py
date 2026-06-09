"""Pump the GLib main context so GStreamer bus/appsink callbacks run under Qt."""
from PyQt6.QtCore import QTimer

import gi
gi.require_version("GLib", "2.0")
from gi.repository import GLib


def attach_glib_pump(app, interval_ms: int = 20):
    """Integrate GLib with the Qt event loop (required for GStreamer previews)."""
    context = GLib.MainContext.default()

    def pump():
        while context.iteration(False):
            pass

    timer = QTimer(app)
    timer.timeout.connect(pump)
    timer.start(interval_ms)
    return timer
