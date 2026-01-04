"""GStreamer audio capture pipeline for microphone and game audio."""
import gi
gi.require_version('Gst', '1.0')
from gi.repository import Gst
from typing import Optional, List


class AudioCapture:
    """Manages GStreamer pipeline for audio capture."""
    
    def __init__(self, source_name: str, source_type: str = 'source'):
        """
        Initialize audio capture.
        
        Args:
            source_name: PulseAudio source/sink name
            source_type: 'source' for microphone, 'sink' for game audio
        """
        self.source_name = source_name
        self.source_type = source_type
        self.pipeline: Optional[Gst.Pipeline] = None
        self.is_running = False
        
        Gst.init(None)
    
    def build_pipeline(self) -> Gst.Pipeline:
        """Build GStreamer pipeline for audio capture."""
        pipeline = Gst.Pipeline.new(f"audio-capture-{self.source_type}")
        
        if self.source_type == 'source':
            # Capture from PulseAudio source (microphone)
            source = Gst.ElementFactory.make("pulsesrc", "audio-source")
            source.set_property("device", self.source_name)
        else:
            # Capture from PulseAudio sink monitor (game audio)
            source = Gst.ElementFactory.make("pulsesrc", "audio-source")
            source.set_property("device", f"{self.source_name}.monitor")
        
        # Audio converter
        audioconvert = Gst.ElementFactory.make("audioconvert", "audio-convert")
        
        # Audio resampler
        audioresample = Gst.ElementFactory.make("audioresample", "audio-resample")
        
        # Audio format
        caps = Gst.Caps.from_string("audio/x-raw,format=S16LE,channels=2,rate=48000")
        caps_filter = Gst.ElementFactory.make("capsfilter", "audio-caps")
        caps_filter.set_property("caps", caps)
        
        # Queue for buffering
        queue = Gst.ElementFactory.make("queue", "audio-queue")
        
        pipeline.add(source)
        pipeline.add(audioconvert)
        pipeline.add(audioresample)
        pipeline.add(caps_filter)
        pipeline.add(queue)
        
        source.link(audioconvert)
        audioconvert.link(audioresample)
        audioresample.link(caps_filter)
        caps_filter.link(queue)
        
        return pipeline
    
    def get_source_pad(self) -> Optional[Gst.Pad]:
        """Get the source pad for mixing pipeline."""
        if not self.pipeline or not self.is_running:
            return None
        
        queue = self.pipeline.get_by_name("audio-queue")
        if queue:
            return queue.get_static_pad("src")
        return None
    
    def start(self):
        """Start the audio capture pipeline."""
        if self.is_running:
            return
        
        self.pipeline = self.build_pipeline()
        
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError(f"Failed to start audio capture pipeline for {self.source_name}")
        
        self.is_running = True
    
    def stop(self):
        """Stop the audio capture pipeline."""
        if not self.is_running or not self.pipeline:
            return
        
        self.pipeline.set_state(Gst.State.NULL)
        self.is_running = False
        self.pipeline = None


class AudioMixer:
    """Mixes multiple audio sources into a single stream."""
    
    def __init__(self, audio_captures: List[AudioCapture]):
        """
        Initialize audio mixer.
        
        Args:
            audio_captures: List of AudioCapture instances to mix
        """
        self.audio_captures = audio_captures
        self.mixer: Optional[Gst.Element] = None
        self.pipeline: Optional[Gst.Pipeline] = None
        self.is_running = False
        
        Gst.init(None)
    
    def build_pipeline(self) -> Gst.Pipeline:
        """Build GStreamer pipeline for audio mixing."""
        pipeline = Gst.Pipeline.new("audio-mixer")
        
        # Create audio mixer element
        self.mixer = Gst.ElementFactory.make("audiomixer", "mixer")
        
        # Add mixer to pipeline
        pipeline.add(self.mixer)
        
        # Link all audio sources to mixer
        for i, capture in enumerate(self.audio_captures):
            if capture.is_running:
                source_pad = capture.get_source_pad()
                if source_pad:
                    sink_pad = self.mixer.get_request_pad(f"sink_{i}")
                    source_pad.link(sink_pad)
        
        # Audio converter and resampler after mixing
        audioconvert = Gst.ElementFactory.make("audioconvert", "mix-convert")
        audioresample = Gst.ElementFactory.make("audioresample", "mix-resample")
        
        pipeline.add(audioconvert)
        pipeline.add(audioresample)
        
        mixer_pad = self.mixer.get_static_pad("src")
        mixer_pad.link(audioconvert.get_static_pad("sink"))
        audioconvert.link(audioresample)
        
        return pipeline
    
    def get_source_pad(self) -> Optional[Gst.Pad]:
        """Get the source pad for recording pipeline."""
        if not self.pipeline or not self.is_running:
            return None
        
        audioresample = self.pipeline.get_by_name("mix-resample")
        if audioresample:
            return audioresample.get_static_pad("src")
        return None
    
    def start(self):
        """Start the audio mixer pipeline."""
        if self.is_running:
            return
        
        # Ensure all audio captures are running
        for capture in self.audio_captures:
            if not capture.is_running:
                capture.start()
        
        self.pipeline = self.build_pipeline()
        
        ret = self.pipeline.set_state(Gst.State.PLAYING)
        if ret == Gst.StateChangeReturn.FAILURE:
            raise RuntimeError("Failed to start audio mixer pipeline")
        
        self.is_running = True
    
    def stop(self):
        """Stop the audio mixer pipeline."""
        if not self.is_running or not self.pipeline:
            return
        
        self.pipeline.set_state(Gst.State.NULL)
        self.is_running = False
        self.pipeline = None
        
        # Stop individual captures
        for capture in self.audio_captures:
            capture.stop()

