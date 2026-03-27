# Copyright (C) 2009, Aleksey Lim
# Copyright (C) 2019, Chihurumnaya Ibiam <ibiamchihurumnaya@sugarlabs.org>
# Copyright (C) 2025, Mebin J Thattil <mail@mebin.in>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 2 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 51 Franklin St, Fifth Floor, Boston, MA  02110-1301  USA

import numpy
import threading

from gi.repository import Gst
from gi.repository import GLib
from gi.repository import GObject

import logging
logger = logging.getLogger('speak')

from sugar3.speech import GstSpeechPlayer

# Kokoro TTS imports
try:
    from kokoro import KPipeline
    KOKORO_AVAILABLE = True
except ImportError:
    KOKORO_AVAILABLE = False
    logger.warning("Kokoro not available, falling back to espeak")

PITCH_MIN = 0
PITCH_MAX = 200
RATE_MIN = 0
RATE_MAX = 200

# Use a constant for the default voice as suggested by Mebin
DEFAULT_KOKORO_VOICE = 'af_heart'


class Speech(GstSpeechPlayer):
    __gsignals__ = {
        'peak': (GObject.SIGNAL_RUN_FIRST, None, [GObject.TYPE_PYOBJECT]),
        'wave': (GObject.SIGNAL_RUN_FIRST, None, [GObject.TYPE_PYOBJECT]),
        'idle': (GObject.SIGNAL_RUN_FIRST, None, []),
    }

    def __init__(self):
        GstSpeechPlayer.__init__(self)
        self.pipeline = None
        
        # Initialize Kokoro pipeline if available
        self.kokoro_pipeline = None
        if KOKORO_AVAILABLE:
            threading.Thread(target=self.setup_kokoro).start()
        
        # Predefined Kokoro voices
        self.kokoro_voices = [
            'af_heart', 'af_alloy', 'af_aoede', 'af_bella', 'af_jessica', 'af_kore', 'af_nicole',
            'af_nova', 'af_river', 'af_sarah', 'af_sky','am_adam', 'am_echo', 'am_eric', 'am_fenrir',
            'am_liam', 'am_michael', 'am_onyx',
            'am_puck', 'am_santa', 'bf_alice', 'bf_emma', 'bf_isabella', 'bf_lily', 'bm_daniel',
            'bm_fable', 'bm_george', 'bm_lewis', 'jf_alpha', 'jf_gongitsune', 'jf_nezumi', 'jf_tebukuro',
            'jm_kumo', 'zf_xiaobei', 'zf_xiaoni', 'zf_xiaoxiao', 'zf_xiaoyi', 'zm_yunjian',
            'zm_yunxi', 'zm_yunxia', 'zm_yunyang', 'ef_dora', 'em_alex', 'em_santa',
            'ff_siwis', 'hf_alpha', 'hf_beta', 'hm_omega', 'hm_psi',
            'if_sara', 'im_nicola', 'pf_dora', 'pm_alex', 'pm_santa'
        ]
        self.current_kokoro_voice = DEFAULT_KOKORO_VOICE

        self._cb = {}
        for cb in ['peak', 'wave', 'idle']:
            self._cb[cb] = None

    def setup_kokoro(self):
        self.kokoro_pipeline = KPipeline(lang_code='a')

    def disconnect_all(self):
        for cb in ['peak', 'wave', 'idle']:
            hid = self._cb[cb]
            if hid is not None:
                self.disconnect(hid)
                self._cb[cb] = None

    def connect_peak(self, cb):
        self._cb['peak'] = self.connect('peak', cb)

    def connect_wave(self, cb):
        self._cb['wave'] = self.connect('wave', cb)

    def connect_idle(self, cb):
        self._cb['idle'] = self.connect('idle', cb)

    def set_kokoro_voice(self, voice_name):
        if voice_name in self.kokoro_voices:
            self.current_kokoro_voice = voice_name
            logger.debug(f"Kokoro voice set to: {voice_name}")
        else:
            logger.warning(
                f"Invalid Kokoro voice: {voice_name}. "
                f"Falling back to default voice: {DEFAULT_KOKORO_VOICE}"
            )
            self.current_kokoro_voice = DEFAULT_KOKORO_VOICE

    def get_available_kokoro_voices(self):
        return self.kokoro_voices.copy()

    def get_default_kokoro_voices(self):
        """Return the default Kokoro voices for UI display."""
        return ['af_heart', 'af_alloy', 'af_aoede']

    def get_addon_kokoro_voices(self):
        """Return the add-on Kokoro voices for UI display."""
        return [v for v in self.kokoro_voices if v not in self.get_default_kokoro_voices()]

    def make_pipeline(self):
        if self.pipeline is not None:
            self.stop_sound_device()
            del self.pipeline

        if KOKORO_AVAILABLE and self.kokoro_pipeline:
            cmd = 'appsrc name=kokoro_src' \
                ' ! audioconvert' \
                ' ! audio/x-raw,channels=(int)1,format=F32LE,rate=24000' \
                ' ! tee name=me' \
                ' me.! queue ! autoaudiosink name=ears' \
                ' me.! queue ! audioconvert ! audioresample ! audio/x-raw,format=S16LE,channels=1,rate=16000 ! fakesink name=sink'
            
        else:
            cmd = 'espeak name=espeak' \
                ' ! capsfilter name=caps' \
                ' ! tee name=me' \
                ' me.! queue ! autoaudiosink name=ears' \
                ' me.! queue ! fakesink name=sink'
            
        self.pipeline = Gst.parse_launch(cmd)
        
        if not (KOKORO_AVAILABLE and self.kokoro_pipeline):
            caps = self.pipeline.get_by_name('caps')
            want = 'audio/x-raw,channels=(int)1,depth=(int)16'
            caps.set_property('caps', Gst.caps_from_string(want))

        ears = self.pipeline.get_by_name('ears')

        def handoff(element, data, pad):
            size = data.get_size()

            if size == 0:
                logger.debug("Size is equal to zero, skipping handoff")
                return True

            if ( data.duration == 0 
                or data.duration == Gst.CLOCK_TIME_NONE 
                or data.duration > Gst.SECOND * 10
            ):
                logger.debug("Invalid duration detected, using fallback")
                SAMPLE_RATE = 16000
                samples = size // 2
                actual_duration = samples * Gst.SECOND // SAMPLE_RATE
            else:
                actual_duration = data.duration

            npc = 50000000 
            bpc = size * npc // actual_duration
            bpc = bpc // 2 * 2

            if bpc == 0:
                bpc = min(4096, size)
                bpc = bpc // 2 * 2

            a, p, w = [], [], []
            here = 0
            when = data.pts
            last = data.pts + actual_duration
            
            while True:
                # Mebin's Suggested Fix: Pre-initialize variables
                raw_bytes = None
                wave = None
                peak = None

                try:
                    raw_bytes = data.extract_dup(here, bpc)
                    
                    if not raw_bytes or len(raw_bytes) == 0:
                        break
                    
                    wave = numpy.frombuffer(raw_bytes, dtype='int16')
                    if len(wave) == 0:
                        break
                        
                    peak = numpy.max(numpy.abs(wave))

                except (ValueError, TypeError, Exception) as e:
                    # Mebin's Suggested Fix: Detailed Error Reporting
                    logger.error(
                        f"Handoff failed: {e} | "
                        f"State: bytes={'present' if raw_bytes else 'None'}, "
                        f"wave={'present' if wave is not None else 'None'}"
                    )
                    break

                # Mebin's Suggested Fix: Guard clause to prevent corrupt data
                if wave is not None and peak is not None:
                    a.append(wave)
                    p.append(peak)
                    w.append(when)

                here += bpc
                when += npc
                if when < last:
                    continue
                break

            def poke(pts):
                success, position = ears.query_position(Gst.Format.TIME)
                if not success:
                    if len(w) > 0:
                        self.emit("wave", a[0])
                        self.emit("peak", p[0])
                        del a[0], w[0], p[0]
                        if len(w) > 0:
                            GLib.timeout_add(25, poke, pts)
                        return False
                    return False

                if len(w) == 0: return False
                if position < w[0]: return True

                self.emit("wave", a[0])
                self.emit("peak", p[0])
                del a[0], w[0], p[0]

                return len(w) > 0

            total_chunks = len(a)
            interval_ms = max(10, int(actual_duration / total_chunks / 1000000)) if total_chunks > 0 else 25

            def emit_next_chunk():
                if len(a) > 0:
                    self.emit("wave", a[0])
                    self.emit("peak", p[0])
                    del a[0], p[0], w[0]
                    if len(a) > 0:
                        GLib.timeout_add(interval_ms, emit_next_chunk)
                return False

            if KOKORO_AVAILABLE and self.kokoro_pipeline:
                GLib.timeout_add(interval_ms, emit_next_chunk)
            else:
                GLib.timeout_add(25, poke, data.pts)

            return True

        sink = self.pipeline.get_by_name('sink')
        sink.props.signal_handoffs = True
        sink.connect('handoff', handoff)

        def gst_message_cb(bus, message):
            self._was_message = True
            if message.type == Gst.MessageType.WARNING:
                def check_after_warnings():
                    if not self._was_message:
                        self.stop_sound_device()
                    return True
                self._was_message = False
                GLib.timeout_add(500, check_after_warnings)
            elif message.type in (Gst.MessageType.EOS, Gst.MessageType.ERROR):
                self.stop_sound_device()
            return True

        self._was_message = False
        bus = self.pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect('message', gst_message_cb)

    def _stream_kokoro_audio(self, text, voice):
        try:
            appsrc = self.pipeline.get_by_name('kokoro_src')
            if not appsrc: return
            
            caps = Gst.Caps.from_string("audio/x-raw,format=F32LE,layout=interleaved,rate=24000,channels=1")
            appsrc.set_property("caps", caps)

            audio_generator = self.kokoro_pipeline(text, voice=voice)

            for i, (gs, ps, audio_chunk) in enumerate(audio_generator):
                data_bytes = audio_chunk.numpy().tobytes()
                buf = Gst.Buffer.new_wrapped(data_bytes)
                ret = appsrc.emit("push-buffer", buf)
                if ret != Gst.FlowReturn.OK: break

            appsrc.emit("end-of-stream")
            
        except Exception as e:
            logger.error(f"Error in Kokoro audio streaming: {e}")
            if appsrc: appsrc.emit("end-of-stream")

    def speak(self, status, text):
        self.make_pipeline()
        if KOKORO_AVAILABLE and self.kokoro_pipeline:
            self.restart_sound_device()
            self._stream_kokoro_audio(text, self.current_kokoro_voice)
        else:
            src = self.pipeline.get_by_name('espeak')
            src.props.pitch = int(status.pitch) - 100
            src.props.rate = int(status.rate) - 100
            src.props.voice = status.voice.name
            src.props.text = text
            self.restart_sound_device()

_speech = None

def get_speech():
    global _speech
    if _speech is None:
        _speech = Speech()
    return _speech
