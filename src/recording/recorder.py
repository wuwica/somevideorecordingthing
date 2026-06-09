"""Main recording controller orchestrating video/audio capture and composition."""
import logging
import gi

log = logging.getLogger(__name__)
gi.require_version('Gst', '1.0')
from gi.repository import Gst
from typing import Optional, List, TYPE_CHECKING
import os
from datetime import datetime

from src.capture.v4l2_pipeline import add_v4l2_source_branch, configure_compositor
from src.composition.layout_engine import LayoutEngine
from src.composition.overlay_manager import OverlayManager
from src.composition.transform import add_circle_mask, add_polygon_mask, add_rect_mask


def _apply_mask(pipeline, prefix: str, prev, source_config: dict):
    """Apply mask from source config. Returns last linked element."""
    mask_shape = source_config.get("mask_shape", "rect")
    mp = source_config.get("mask_position") or {}
    ms = source_config.get("mask_size") or {}
    mx, my = mp.get("x"), mp.get("y")
    mw, mh = ms.get("width"), ms.get("height")
    has_area = mx is not None and my is not None and mw and mh
    if mask_shape == "circle":
        return add_circle_mask(pipeline, prefix, prev,
                               mx if has_area else None, my if has_area else None,
                               mw if has_area else None, mh if has_area else None)
    if mask_shape == "rect" and has_area:
        return add_rect_mask(pipeline, prefix, prev, mx, my, mw, mh)
    if mask_shape == "polygon":
        pts = source_config.get("mask_points") or []
        if len(pts) >= 3:
            return add_polygon_mask(pipeline, prefix, prev, pts)
    return prev

if TYPE_CHECKING:
    from src.recording.trigger_manager import TriggerManager
    from src.preview.preview_manager import PreviewManager


class Recorder:
    """Main recording controller."""

    def __init__(self, config_path: str, output_directory: str = "./recordings"):
        self.config_path = config_path
        self.output_directory = output_directory
        self.layout_engine = LayoutEngine(config_path)
        # List of dicts with keys: source_name (str), source_type ('source'|'sink')
        self.audio_captures: List[dict] = []
        self.overlay_manager: Optional[OverlayManager] = None
        self._trigger_manager: Optional["TriggerManager"] = None
        self._preview_manager: Optional["PreviewManager"] = None

        self.recording_pipeline: Optional[Gst.Pipeline] = None
        self.is_recording = False
        self.current_output_path: Optional[str] = None

        os.makedirs(self.output_directory, exist_ok=True)

        Gst.init(None)
        self._setup_from_config()

    def set_trigger_manager(self, trigger_manager: "TriggerManager"):
        """Inject the TriggerManager used for audio/frame stop triggers."""
        self._trigger_manager = trigger_manager

    def set_preview_manager(self, preview_manager: "PreviewManager"):
        """Inject PreviewManager for live preview tee during recording."""
        self._preview_manager = preview_manager
    
    def _setup_from_config(self):
        """Setup components based on configuration."""
        config = self.layout_engine.config
        overlays_config = config.get("overlays", [])
        self.overlay_manager = OverlayManager(overlays_config)
    
    def setup_audio(self, mic_source: Optional[str] = None, game_audio_source: Optional[str] = None):
        """Setup audio capture sources. Only adds sources when actual device names are detected."""
        config = self.layout_engine.config
        audio_config = config.get("audio", {})

        self.audio_captures.clear()

        # Only add audio when a real device name was discovered — "default" with no
        # PulseAudio running causes the recording pipeline to fail immediately.
        if audio_config.get("mic_enabled", True) and mic_source:
            self.audio_captures.append({"source_name": mic_source, "source_type": "source"})
        elif audio_config.get("mic_enabled", True):
            log.warning("Mic audio disabled — no PulseAudio device detected")

        if audio_config.get("game_audio_enabled", True) and game_audio_source:
            self.audio_captures.append({"source_name": game_audio_source, "source_type": "sink"})
        elif audio_config.get("game_audio_enabled", True):
            log.warning("Game audio disabled — no PulseAudio device detected")
    
    def build_recording_pipeline(self) -> Gst.Pipeline:
        """Build the complete recording pipeline."""
        pipeline = Gst.Pipeline.new("recording-pipeline")
        
        # Get output configuration
        output_config = self.layout_engine.config.get("output", {})
        width = output_config.get("width", 1920)
        height = output_config.get("height", 1080)
        fps = output_config.get("fps", 30)
        
        # Create compositor
        compositor = Gst.ElementFactory.make("compositor", "compositor")
        configure_compositor(compositor)
        pipeline.add(compositor)
        
        # Build video sources and link to compositor
        sources_config = self.layout_engine.config.get("sources", [])
        compositor_index = 0
        for source_config in sources_config:
            source_id = source_config.get("id")
            device_path = source_config.get("device")
            if not device_path or not os.path.exists(device_path):
                log.warning("Skipping %s (%s not available)", source_id, device_path)
                continue

            size = source_config.get("size", {"width": width, "height": height})
            target_w = size.get("width", width)
            target_h = size.get("height", height)

            source_fps = source_config.get("fps") or fps
            caps_filter, _fmt = add_v4l2_source_branch(
                pipeline,
                f"rec-{source_id}",
                device_path,
                target_w,
                target_h,
                fps=source_fps,
                capabilities=source_config.get("capabilities"),
                source_type=source_config.get("type", ""),
                formats=source_config.get("formats"),
                input_width=source_config.get("capture_width"),
                input_height=source_config.get("capture_height"),
                input_format=source_config.get("capture_format"),
                rotation=source_config.get("rotation", 0),
            )
            if not caps_filter:
                log.error("Could not link %s (%s)", source_id, device_path)
                continue

            last = caps_filter
            last = _apply_mask(pipeline, f"rec-{source_id}", last, source_config)

            # HDMI-only frame detection branch (tee → scale 160×120 → GRAY8 → appsink)
            is_hdmi = source_config.get("type") == "hdmi"
            if is_hdmi and self._trigger_manager and self._trigger_manager.frame_ready:
                tee = Gst.ElementFactory.make("tee", f"rec-{source_id}-frame-tee")
                q_comp = Gst.ElementFactory.make("queue", f"rec-{source_id}-frame-q-comp")
                q_det  = Gst.ElementFactory.make("queue", f"rec-{source_id}-frame-q-det")
                q_det.set_property("max-size-buffers", 1)
                q_det.set_property("leaky", 2)
                det_scale   = Gst.ElementFactory.make("videoscale",   f"rec-{source_id}-det-scale")
                det_conv    = Gst.ElementFactory.make("videoconvert",  f"rec-{source_id}-det-conv")
                det_caps_el = Gst.ElementFactory.make("capsfilter",    f"rec-{source_id}-det-caps")
                det_caps_el.set_property(
                    "caps", Gst.Caps.from_string("video/x-raw,format=GRAY8,width=160,height=120")
                )
                det_sink = Gst.ElementFactory.make("appsink", f"rec-{source_id}-det-sink")
                det_sink.set_property("emit-signals", True)
                det_sink.set_property("max-buffers", 1)
                det_sink.set_property("drop", True)
                det_sink.set_property("sync", False)

                for el in (tee, q_comp, q_det, det_scale, det_conv, det_caps_el, det_sink):
                    pipeline.add(el)

                last.link(tee)
                tee.link(q_comp)
                det_tee_pad = tee.request_pad_simple("src_%u")
                if det_tee_pad is None:
                    log.error("Failed to request tee src pad for frame detector on %s", source_id)
                else:
                    det_tee_pad.link(q_det.get_static_pad("sink"))
                q_det.link(det_scale)
                det_scale.link(det_conv)
                det_conv.link(det_caps_el)
                det_caps_el.link(det_sink)

                trigger_mgr = self._trigger_manager

                def _on_frame(appsink, _tm=trigger_mgr):
                    sample = appsink.emit("pull-sample")
                    if sample:
                        buf = sample.get_buffer()
                        ok, mapinfo = buf.map(Gst.MapFlags.READ)
                        if ok:
                            try:
                                _tm.add_frame(bytes(mapinfo.data), 160, 120)
                            finally:
                                buf.unmap(mapinfo)
                    return Gst.FlowReturn.OK

                det_sink.connect("new-sample", _on_frame)
                last = q_comp  # compositor gets frames from the main tee branch

            sink_pad = compositor.request_pad_simple(f"sink_{compositor_index}")
            if sink_pad is None:
                log.error("Failed to request compositor sink pad %d", compositor_index)
                continue  # skip this source

            position = source_config.get("position", {"x": 0, "y": 0})
            z_order = source_config.get("z_order", compositor_index)

            sink_pad.set_property("xpos", position.get("x", 0))
            sink_pad.set_property("ypos", position.get("y", 0))
            sink_pad.set_property("width", target_w)
            sink_pad.set_property("height", target_h)
            sink_pad.set_property("zorder", z_order)

            last.get_static_pad("src").link(sink_pad)

            self.layout_engine.compositor = compositor
            compositor_index += 1

        # Static image overlays
        if self.overlay_manager:
            compositor_index = self.overlay_manager.add_overlays_to_pipeline(
                pipeline, compositor, compositor_index, fps
            )

        # Video converter after composition
        videoconvert_final = Gst.ElementFactory.make("videoconvert", "final-convert")
        caps_final = Gst.Caps.from_string(
            f"video/x-raw,width={width},height={height},framerate={fps}/1"
        )
        caps_filter_final = Gst.ElementFactory.make("capsfilter", "final-caps")
        caps_filter_final.set_property("caps", caps_final)
        
        pipeline.add(videoconvert_final)
        pipeline.add(caps_filter_final)
        
        compositor_src = compositor.get_static_pad("src")
        compositor_src.link(videoconvert_final.get_static_pad("sink"))
        videoconvert_final.link(caps_filter_final)
        
        # Video encoder
        video_encoder = Gst.ElementFactory.make("x264enc", "video-encoder")
        video_encoder.set_property("tune", "zerolatency")
        video_encoder.set_property("speed-preset", "ultrafast")
        video_encoder.set_property("bitrate", 5000)
        
        # Video parser
        video_parser = Gst.ElementFactory.make("h264parse", "video-parser")
        
        pipeline.add(video_encoder)
        pipeline.add(video_parser)

        if self._preview_manager:
            video_tee = Gst.ElementFactory.make("tee", "video-tee")
            queue_enc = Gst.ElementFactory.make("queue", "video-queue-enc")
            pipeline.add(video_tee)
            pipeline.add(queue_enc)
            caps_filter_final.link(video_tee)
            video_tee.link(queue_enc)
            queue_enc.link(video_encoder)
            self._preview_manager.add_preview_branch(pipeline, video_tee, prefix="preview-rec")
        else:
            caps_filter_final.link(video_encoder)
        video_encoder.link(video_parser)
        
        # Audio encoder (if audio sources exist)
        audio_parser = None
        if self.audio_captures:
            # Create audio mixer element in main pipeline
            audio_mixer = Gst.ElementFactory.make("audiomixer", "audio-mixer")
            pipeline.add(audio_mixer)
            
            # Add each audio source to pipeline and link to mixer
            for i, audio_capture in enumerate(self.audio_captures):
                source = Gst.ElementFactory.make("pulsesrc", f"audio-source-{i}")
                if source is None:
                    log.warning("pulsesrc plugin unavailable — skipping audio source %d", i)
                    continue
                if audio_capture["source_type"] == "source":
                    source.set_property("device", audio_capture["source_name"])
                else:
                    source.set_property("device", f"{audio_capture['source_name']}.monitor")
                
                audioconvert = Gst.ElementFactory.make("audioconvert", f"audio-convert-{i}")
                audioresample = Gst.ElementFactory.make("audioresample", f"audio-resample-{i}")
                caps = Gst.Caps.from_string("audio/x-raw,format=S16LE,channels=2,rate=48000")
                caps_filter = Gst.ElementFactory.make("capsfilter", f"audio-caps-{i}")
                caps_filter.set_property("caps", caps)
                
                pipeline.add(source)
                pipeline.add(audioconvert)
                pipeline.add(audioresample)
                pipeline.add(caps_filter)
                
                source.link(audioconvert)
                audioconvert.link(audioresample)
                audioresample.link(caps_filter)
                
                # Link to mixer
                sink_pad = audio_mixer.request_pad_simple(f"sink_{i}")
                if sink_pad is None:
                    log.error("Failed to request audiomixer sink pad %d", i)
                    continue
                src_pad = caps_filter.get_static_pad("src")
                src_pad.link(sink_pad)
            
            # Audio converter after mixing
            audio_convert_final = Gst.ElementFactory.make("audioconvert", "audio-convert-final")
            audio_resample_final = Gst.ElementFactory.make("audioresample", "audio-resample-final")
            
            pipeline.add(audio_convert_final)
            pipeline.add(audio_resample_final)
            
            mixer_src = audio_mixer.get_static_pad("src")
            mixer_src.link(audio_convert_final.get_static_pad("sink"))
            audio_convert_final.link(audio_resample_final)
            
            # Tee: branch 1 → encoder, branch 2 → trigger appsink
            audio_tee = Gst.ElementFactory.make("tee", "audio-tee")
            pipeline.add(audio_tee)
            audio_resample_final.link(audio_tee)

            # Branch 1: encode → mux
            queue_enc = Gst.ElementFactory.make("queue", "audio-queue-enc")
            pipeline.add(queue_enc)
            audio_tee.link(queue_enc)

            audio_encoder = Gst.ElementFactory.make("avenc_aac", "audio-encoder")
            audio_encoder.set_property("bitrate", 128000)
            audio_parser = Gst.ElementFactory.make("aacparse", "audio-parser")

            pipeline.add(audio_encoder)
            pipeline.add(audio_parser)

            queue_enc.link(audio_encoder)
            audio_encoder.link(audio_parser)

            # Branch 2: feed raw PCM to AudioTrigger (only wired when trigger is active)
            if self._trigger_manager and self._trigger_manager.audio_ready:
                queue_trigger = Gst.ElementFactory.make("queue", "audio-queue-trigger")
                queue_trigger.set_property("max-size-buffers", 4)
                queue_trigger.set_property("leaky", 2)  # leak downstream (drop old buffers)
                audio_appsink = Gst.ElementFactory.make("appsink", "audio-trigger-sink")
                audio_appsink.set_property("emit-signals", True)
                audio_appsink.set_property("max-buffers", 2)
                audio_appsink.set_property("drop", True)
                audio_appsink.set_property(
                    "caps",
                    Gst.Caps.from_string("audio/x-raw,format=S16LE,channels=2,rate=48000"),
                )

                trigger_mgr = self._trigger_manager

                def _on_audio_sample(appsink):
                    sample = appsink.emit("pull-sample")
                    if sample:
                        buf = sample.get_buffer()
                        ok, mapinfo = buf.map(Gst.MapFlags.READ)
                        if ok:
                            try:
                                trigger_mgr.add_audio_chunk(bytes(mapinfo.data), channels=2)
                            finally:
                                buf.unmap(mapinfo)
                    return Gst.FlowReturn.OK

                audio_appsink.connect("new-sample", _on_audio_sample)

                pipeline.add(queue_trigger)
                pipeline.add(audio_appsink)
                audio_tee.link(queue_trigger)
                queue_trigger.link(audio_appsink)
        
        # Muxer
        muxer = Gst.ElementFactory.make("mp4mux", "muxer")
        muxer.set_property("faststart", True)
        
        # File sink
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"recording_{timestamp}.mp4"
        self.current_output_path = os.path.join(self.output_directory, filename)
        
        filesink = Gst.ElementFactory.make("filesink", "file-sink")
        filesink.set_property("location", self.current_output_path)
        
        pipeline.add(muxer)
        pipeline.add(filesink)
        
        # Link video to muxer
        video_parser.link(muxer)
        
        # Link audio to muxer if available
        if audio_parser:
            audio_parser.link(muxer)
        
        # Link muxer to file sink
        muxer.link(filesink)
        
        return pipeline
    
    def start_recording(self, output_path: Optional[str] = None):
        """Start recording."""
        if self.is_recording:
            return
        
        if output_path:
            self.current_output_path = output_path
        
        try:
            self.recording_pipeline = self.build_recording_pipeline()
            ret = self.recording_pipeline.set_state(Gst.State.PLAYING)

            if ret == Gst.StateChangeReturn.FAILURE:
                bus = self.recording_pipeline.get_bus()
                msg = bus.pop_filtered(Gst.MessageType.ERROR)
                if msg:
                    err, dbg = msg.parse_error()
                    log.error("Pipeline error: %s | %s", err, dbg)
                raise RuntimeError("Failed to start recording pipeline")

            self.is_recording = True

            if self._trigger_manager:
                self._trigger_manager.start_monitoring()

            log.info("Recording started: %s", self.current_output_path)

        except Exception as e:
            log.error("Error starting recording: %s", e, exc_info=True)
            self.stop_recording()
            raise
    
    def stop_recording(self):
        """Stop recording."""
        if not self.is_recording:
            return

        if self._trigger_manager:
            self._trigger_manager.stop_monitoring()

        if self.recording_pipeline:
            self.recording_pipeline.set_state(Gst.State.NULL)
            self.recording_pipeline = None

        self.is_recording = False
        log.info("Recording stopped: %s", self.current_output_path)
    
    def get_output_path(self) -> Optional[str]:
        """Get current recording output path."""
        return self.current_output_path

