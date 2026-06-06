"""Main recording controller orchestrating video/audio capture and composition."""
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
from typing import Optional, Dict, List, TYPE_CHECKING
import os
from datetime import datetime

from src.capture.audio_capture import AudioCapture, AudioMixer
from src.composition.layout_engine import LayoutEngine
from src.composition.overlay_manager import OverlayManager

if TYPE_CHECKING:
    from src.recording.trigger_manager import TriggerManager


class Recorder:
    """Main recording controller."""

    def __init__(self, config_path: str, output_directory: str = "./recordings"):
        self.config_path = config_path
        self.output_directory = output_directory
        self.layout_engine = LayoutEngine(config_path)
        self.audio_captures: List[AudioCapture] = []
        self.audio_mixer: Optional[AudioMixer] = None
        self.overlay_manager: Optional[OverlayManager] = None
        self._trigger_manager: Optional["TriggerManager"] = None

        self.recording_pipeline: Optional[Gst.Pipeline] = None
        self.is_recording = False
        self.current_output_path: Optional[str] = None

        os.makedirs(self.output_directory, exist_ok=True)

        Gst.init(None)
        self._setup_from_config()

    def set_trigger_manager(self, trigger_manager: "TriggerManager"):
        """Inject the TriggerManager used for audio/frame stop triggers."""
        self._trigger_manager = trigger_manager
    
    def _setup_from_config(self):
        """Setup components based on configuration."""
        config = self.layout_engine.config
        overlays_config = config.get("overlays", [])
        self.overlay_manager = OverlayManager(overlays_config)
    
    def add_video_source(self, device_path: str, source_id: str):
        """Register a video source for inclusion in the next recording pipeline."""
        # Sources are built directly from layout_engine.config in build_recording_pipeline.
        # This method exists so RecordingController can declare devices; the config is the
        # authoritative source of device paths actually used in the pipeline.
    
    def setup_audio(self, mic_source: Optional[str] = None, game_audio_source: Optional[str] = None):
        """Setup audio capture sources."""
        config = self.layout_engine.config
        audio_config = config.get("audio", {})
        
        # Clear existing audio captures
        self.audio_captures.clear()
        
        # Add microphone if enabled
        if audio_config.get("mic_enabled", True):
            mic_name = mic_source or audio_config.get("mic_source", "default")
            mic_capture = AudioCapture(mic_name, source_type='source')
            self.audio_captures.append(mic_capture)
        
        # Add game audio if enabled
        if audio_config.get("game_audio_enabled", True):
            game_name = game_audio_source or audio_config.get("game_audio_source", "default")
            game_capture = AudioCapture(game_name, source_type='sink')
            self.audio_captures.append(game_capture)
        
        # Create audio mixer if we have audio sources
        if self.audio_captures:
            self.audio_mixer = AudioMixer(self.audio_captures)
    
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
        pipeline.add(compositor)
        
        # Build video sources and link to compositor
        sources_config = self.layout_engine.config.get("sources", [])
        for i, source_config in enumerate(sources_config):
            source_id = source_config.get("id")
            device_path = source_config.get("device")
            
            # Create video source pipeline
            source = Gst.ElementFactory.make("v4l2src", f"source-{source_id}")
            source.set_property("device", device_path)
            
            videoconvert = Gst.ElementFactory.make("videoconvert", f"convert-{source_id}")
            videoscale = Gst.ElementFactory.make("videoscale", f"scale-{source_id}")
            
            # Set source size
            size = source_config.get("size", {"width": width, "height": height})
            caps = Gst.Caps.from_string(
                f"video/x-raw,width={size.get('width', width)},height={size.get('height', height)},framerate={fps}/1"
            )
            caps_filter = Gst.ElementFactory.make("capsfilter", f"caps-{source_id}")
            caps_filter.set_property("caps", caps)
            
            pipeline.add(source)
            pipeline.add(videoconvert)
            pipeline.add(videoscale)
            pipeline.add(caps_filter)
            
            source.link(videoconvert)
            videoconvert.link(videoscale)
            videoscale.link(caps_filter)
            
            # Request sink pad from compositor
            sink_pad = compositor.get_request_pad(f"sink_{i}")
            
            # Set pad properties
            position = source_config.get("position", {"x": 0, "y": 0})
            z_order = source_config.get("z_order", i)
            
            sink_pad.set_property("xpos", position.get("x", 0))
            sink_pad.set_property("ypos", position.get("y", 0))
            sink_pad.set_property("width", size.get("width", width))
            sink_pad.set_property("height", size.get("height", height))
            sink_pad.set_property("zorder", z_order)
            
            # Link source to compositor
            src_pad = caps_filter.get_static_pad("src")
            src_pad.link(sink_pad)
            
            # Store compositor reference
            self.layout_engine.compositor = compositor
        
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
                if audio_capture.source_type == 'source':
                    source = Gst.ElementFactory.make("pulsesrc", f"audio-source-{i}")
                    source.set_property("device", audio_capture.source_name)
                else:
                    source = Gst.ElementFactory.make("pulsesrc", f"audio-source-{i}")
                    source.set_property("device", f"{audio_capture.source_name}.monitor")
                
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
                sink_pad = audio_mixer.get_request_pad(f"sink_{i}")
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
                raise RuntimeError("Failed to start recording pipeline")

            self.is_recording = True

            if self._trigger_manager:
                self._trigger_manager.start_monitoring()

            print(f"Recording started: {self.current_output_path}")
        
        except Exception as e:
            print(f"Error starting recording: {e}")
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

        self.audio_captures.clear()

        self.is_recording = False
        print(f"Recording stopped: {self.current_output_path}")
    
    def get_output_path(self) -> Optional[str]:
        """Get current recording output path."""
        return self.current_output_path

