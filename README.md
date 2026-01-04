# HDMI Recording Software

A Linux-based recording application for capturing HDMI input and USB cameras with configurable layouts, audio capture, and USB key-triggered recording.

## Features

- HDMI capture card recording
- USB camera support (multiple cameras)
- Configurable video layouts via JSON
- Image overlay support
- Audio recording (microphone and game audio)
- USB key detection for recording triggers
- Frame comparison for auto-stop functionality
- Touch screen optimized UI

## Requirements

- Linux with GStreamer 1.0+ installed
- Python 3.8+
- PulseAudio for audio capture
- HDMI capture card or HDMI-to-USB adapter

## Installation

1. Install system dependencies:
```bash
sudo apt-get install python3-gst-1.0 gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-plugins-bad gstreamer1.0-plugins-ugly gstreamer1.0-libav gstreamer1.0-tools
```

2. Install Python dependencies:
```bash
pip install -r requirements.txt
```

3. Configure your layout in `config/default_layout.json`

## Usage

Run the application:
```bash
python main.py
```

Insert a USB key to trigger recording prompts.

