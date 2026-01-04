"""Main recording controller orchestrating video/audio capture and composition."""
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
from typing import Optional, Dict, List
import os
from datetime import datetime

from src.capture.video_capture import VideoCaptureManager
from src.capture.audio_capture import AudioCapture, AudioMixer
from src.composition.layout_engine import LayoutEngine
from src.composition.overlay_manager import OverlayManager
from src.recording.frame_detector import FrameDetector


class Recorder:
    """Main recording controller."""
    
    def __init__(self, config_path: str, output_directory: str = "./recordings"):
        """
        Initialize recorder.
        
        Args:
            config_path: Path to layout configuration JSON
            output_directory: Directory to save recordings
        """
        self.config_path = config_path
        self.output_directory = output_directory
        self.layout_engine = LayoutEngine(config_path)
        self.video_manager = VideoCaptureManager()
        self.audio_captures: List[AudioCapture] = []
        self.audio_mixer: Optional[AudioMixer] = None
        self.overlay_manager: Optional[OverlayManager] = None
        self.frame_detector: Optional[FrameDetector] = None
        
        self.recording_pipeline: Optional[Gst.Pipeline] = None
        self.is_recording = False
        self.current_output_path: Optional[str] = None
        
        # Ensure output directory exists
        os.makedirs(self.output_directory, exist_ok=True)
        
        Gst.init(None)
        self._setup_from_config()
    
    def _setup_from_config(self):
        """Setup components based on configuration."""
        config = self.layout_engine.config
        
        # Setup overlays
        overlays_config = config.get("overlays", [])
        self.overlay_manager = OverlayManager(overlays_config)
        
        # Setup frame detector if enabled
        stop_frame_config = config.get("stop_frame", {})
        if stop_frame_config.get("enabled", False):
            frame_image = stop_frame_config.get("image")
            threshold = stop_frame_config.get("threshold", 0.85)
            check_interval = stop_frame_config.get("check_interval", 1.0)
            
            if frame_image and os.path.exists(frame_image):
                self.frame_detector = FrameDetector(frame_image, threshold, check_interval)
                self.frame_detector.set_callback(self.stop_recording)
    
    def add_video_source(self, device_path: str, source_id: str):
        """Add a video source."""
        # Create callback for frame detection if needed
        callback = None
        if self.frame_detector:
            def frame_callback(src_id, frame_data, width, height):
                if src_id == "hdmi":  # Only check HDMI feed
                    self.frame_detector.add_frame(frame_data, width, height)
            callback = frame_callback
        
        self.video_manager.add_source(device_path, source_id, callback)
    
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
            
            # Audio encoder
            audio_encoder = Gst.ElementFactory.make("avenc_aac", "audio-encoder")
            audio_encoder.set_property("bitrate", 128000)
            audio_parser = Gst.ElementFactory.make("aacparse", "audio-parser")
            
            pipeline.add(audio_encoder)
            pipeline.add(audio_parser)
            
            audio_resample_final.link(audio_encoder)
            audio_encoder.link(audio_parser)
        
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
            # Start video captures first (for frame detection)
            self.video_manager.start_all()
            
            # Build and start recording pipeline
            self.recording_pipeline = self.build_recording_pipeline()
            ret = self.recording_pipeline.set_state(Gst.State.PLAYING)
            
            if ret == Gst.StateChangeReturn.FAILURE:
                raise RuntimeError("Failed to start recording pipeline")
            
            self.is_recording = True
            
            # Start frame detection if enabled
            if self.frame_detector:
                self.frame_detector.start_monitoring()
            
            print(f"Recording started: {self.current_output_path}")
        
        except Exception as e:
            print(f"Error starting recording: {e}")
            self.stop_recording()
            raise
    
    def stop_recording(self):
        """Stop recording."""
        if not self.is_recording:
            return
        
        # Stop frame detection
        if self.frame_detector:
            self.frame_detector.stop_monitoring()
        
        # Stop recording pipeline
        if self.recording_pipeline:
            self.recording_pipeline.set_state(Gst.State.NULL)
            self.recording_pipeline = None
        
        # Stop video captures
        self.video_manager.stop_all()
        
        # Stop audio mixer
        if self.audio_mixer:
            self.audio_mixer.stop()
        
        self.is_recording = False
        print(f"Recording stopped: {self.current_output_path}")
    
    def get_output_path(self) -> Optional[str]:
        """Get current recording output path."""
        return self.current_output_path

