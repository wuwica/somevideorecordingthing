# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## New dependencies to install
```bash
pip install imagehash fastapi uvicorn python-multipart
```

## Commands

Install system dependencies (GStreamer):
```bash
sudo apt-get install python3-gst-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-tools
```

Install Python dependencies:
```bash
pip install -r requirements.txt
# or
pipenv install
```

Run the application:
```bash
python main.py                          # uses config/default_layout.json
python main.py config/my_layout.json   # custom layout
```

There are no tests.

Run the admin web UI (served automatically alongside the app):
```
http://localhost:8080   # default; override with ADMIN_PORT=XXXX python main.py
```

## Architecture

The app is a touch-screen-optimized PyQt6 GUI for recording multi-source HDMI/USB-camera video to a USB key on Linux.

### Data flow

```
USB cameras (/dev/videoN) ──┐
HDMI capture (/dev/video0) ──┤── GStreamer compositor ── x264/AAC ── mp4mux ── file
PulseAudio sources/sinks ───┘
```

### Module responsibilities

**`src/controller/`** — `RecordingController(QObject)` is the shared state owner. Both the PyQt6 window and the FastAPI web server call its thread-safe methods (`start_recording()`, `stop_recording()`, `reload_audio_trigger()`, etc.). It emits PyQt6 signals (auto-queued across threads) for the UI and publishes SSE events for the browser. `AppState` is a simple dataclass holding `is_recording`, `output_path`, and `upload_dir`.

**`src/web/`** — FastAPI app run in a daemon thread with its own asyncio loop. `sse.py` bridges GStreamer/USB/trigger callbacks into the asyncio event bus via `call_soon_threadsafe`. REST routers under `routers/`. Static admin SPA served from `web/static/`.



**`src/ui/`** — PyQt6 customer-facing touchscreen. `MainWindow` accepts a `RecordingController` and only handles UI (recording status dot, Start/Stop buttons, USB label). All state lives in the controller. The settings button is replaced with the admin URL label.

**`src/recording/recorder.py`** — `Recorder` builds and runs a single GStreamer pipeline that composites all video sources, mixes audio, encodes H.264+AAC, and muxes to MP4. The pipeline is rebuilt on every `start_recording()` call. When a `TriggerManager` is injected via `set_trigger_manager()`, the audio path includes a `tee` — one branch encodes to MP4, the other feeds raw S16LE PCM to `AudioTrigger.add_audio_chunk()` via an appsink. Output path is `<usb_mount_point>/recording_<timestamp>.mp4`.

**`src/capture/`** — `VideoCapture` wraps a per-source GStreamer pipeline (v4l2src → videoconvert → videoscale → appsink). `VideoCaptureManager` manages a dict of these. These pipelines are only used when a frame-data callback is needed (e.g. for `FrameDetector`); the recording pipeline builds its own v4l2src elements independently.

**`src/composition/`** — `LayoutEngine` loads/reloads the JSON config and owns the `compositor` GStreamer element reference. `OverlayManager` handles image overlays (Pillow-based, from the `overlays` config key).

**`src/recording/frame_detector.py`** — `FrameDetector` compares YUY2 HDMI frames against a reference PNG using **perceptual hashing** (`imagehash.phash`). The reference is pre-hashed at load. Each frame is converted YUY2→PIL Image→phash; Hamming distance ≤ `(1-threshold)*64` triggers the callback. Runs in a daemon thread.

**`src/recording/audio_trigger.py`** — `AudioTrigger` detects a reference WAV clip in the live S16LE/48 kHz audio stream via spectral fingerprinting (averaged log-power FFT, cosine similarity). Requires `scipy`; gracefully disabled if not installed.

**`src/recording/trigger_manager.py`** — `TriggerManager` is the preferred way to configure auto-stop. It selects **AudioTrigger first** (if a clip is loaded and ready), falling back to **FrameDetector**. Only one trigger is active per recording session. Supports hot-reload of either trigger while recording via `reload_audio()` / `reload_frame()`.

**`src/usb/usb_monitor.py`** — `USBMonitor` uses `pyudev` to watch the `block` subsystem for removable USB storage. Mount points are resolved from `/proc/mounts` then `/media` / `/mnt` fallbacks.

**`src/capture/device_manager.py`** — enumerates V4L2 video devices (distinguishes HDMI vs. USB cameras) and PulseAudio sources/sinks at startup.

### Layout configuration (`config/default_layout.json`)

Controls everything about the recording pipeline:
- `sources`: list of V4L2 devices with pixel position, size, and z-order for the compositor
- `overlays`: image overlays composited via `OverlayManager`
- `output`: final resolution and framerate
- `stop_frame`: enables/configures `FrameDetector` (image path + similarity threshold)
- `audio`: enables mic (PulseAudio source) and game audio (PulseAudio sink monitor)

Changing this file and clicking Settings → OK triggers `LayoutEngine.reload_config()` + `Recorder._setup_from_config()` without restarting.

### Key design notes

- `Recorder.build_recording_pipeline()` creates v4l2src elements directly from the `sources` config. `VideoCaptureManager` (`src/capture/`) still exists but is no longer used by `Recorder` — the recording pipeline is self-contained.
- The audio tee in the recording pipeline is only added when `TriggerManager.audio_ready` is True at pipeline-build time. Changing the audio trigger mid-recording requires stopping and restarting.
- `RecordingController` holds the `RLock` that serializes all state mutations across the Qt thread, GStreamer threads, pyudev thread, and FastAPI thread. All cross-thread UI updates use PyQt6 queued signal delivery (automatic when emitting from non-Qt threads).
