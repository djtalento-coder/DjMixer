"""Real-time mixer: main track voices with equal-power crossfades, cart/jingle
overlays with music ducking, and a tap for streaming. render() is device-independent."""
import queue
import threading
import time

import numpy as np

SR = 44100
BLOCK = 1024


class Voice:
    def __init__(self, pcm, info=None, fade_in=0):
        self.pcm = pcm
        self.info = info or {}
        self.pos = 0
        self.length = len(pcm)
        self.fi_len = int(fade_in)
        self.fi_pos = 0
        self.fading_out = False
        self.fo_len = 0
        self.fo_pos = 0

    @property
    def remaining(self):
        return self.length - self.pos

    @property
    def done(self):
        return self.pos >= self.length or (self.fading_out and self.fo_pos >= self.fo_len)

    def start_fade_out(self, n):
        self.fading_out = True
        self.fo_len = max(1, int(n))
        self.fo_pos = 0

    def read(self, n):
        end = min(self.pos + n, self.length)
        m = end - self.pos
        if m <= 0:
            return None
        chunk = self.pcm[self.pos:end]
        g = None
        if self.fi_pos < self.fi_len:
            t = np.clip((self.fi_pos + np.arange(m)) / self.fi_len, 0, 1)
            g = np.sin(t * np.pi / 2)
            self.fi_pos += m
        if self.fading_out:
            t = np.clip((self.fo_pos + np.arange(m)) / self.fo_len, 0, 1)
            go = np.cos(t * np.pi / 2)
            g = go if g is None else g * go
            self.fo_pos += m
        self.pos = end
        if g is not None:
            chunk = chunk * g[:, None].astype(np.float32)
        return chunk


class Engine:
    def __init__(self):
        self.lock = threading.RLock()
        self.events = queue.SimpleQueue()   # ('started', info) ('need_next',) ('idle',)
        self.cur = None
        self.fading = []
        self.carts = []
        self.pending = None
        self.force_next = False
        self.paused = False
        self.accepting = True   # False after stop_all until the controller starts again
        self.crossfade_s = 6.0
        self.duck_level = 0.35
        self.duck_gain = 1.0
        self.master = 1.0
        self.peak = (0.0, 0.0)
        self.tap = None
        self.driver = None
        self.driver_error = ""
        self._stream = None
        self._clock = None
        self._alive = False

    # ---------------- control (any thread) ----------------
    @property
    def has_pending(self):
        return self.pending is not None

    def set_pending(self, pcm, info):
        with self.lock:
            if self.accepting:
                self.pending = {"pcm": pcm, "info": info}

    def skip(self):
        with self.lock:
            self.force_next = True

    def play_cart(self, pcm, info=None):
        with self.lock:
            self.carts.append(Voice(pcm, info))

    def stop_all(self, fade_s=1.5):
        with self.lock:
            n = int(fade_s * SR)
            if self.cur is not None:
                self.cur.start_fade_out(n)
                self.fading.append(self.cur)
                self.cur = None
            for v in self.fading:
                if not v.fading_out:
                    v.start_fade_out(n)
            self.pending = None
            self.force_next = False
            self.accepting = False

    def state(self):
        with self.lock:
            c = self.cur
            if c is None:
                return {"info": None, "pos_s": 0.0, "len_s": 0.0, "fading": False}
            return {"info": c.info, "pos_s": c.pos / SR, "len_s": c.length / SR,
                    "fading": bool(self.fading)}

    # ---------------- mixing (audio thread) ----------------
    def _start_pending(self, fade_in, fo_len):
        p = self.pending
        self.pending = None
        self.force_next = False
        old = self.cur
        if old is not None and not old.done:
            old.start_fade_out(fo_len)
            self.fading.append(old)
        elif fade_in == 0:
            fade_in = 220  # 5 ms de-click when starting from silence
        self.cur = Voice(p["pcm"], p["info"], fade_in)
        self.events.put(("started", p["info"]))
        self.events.put(("need_next",))

    def _maybe_advance(self, n):
        p = self.pending
        if p is None:
            return
        c = self.cur
        if c is None or c.done:
            self._start_pending(0, 0)
            return
        if c.fading_out:
            return
        xf = min(int(self.crossfade_s * SR), c.length // 2, len(p["pcm"]) // 2)
        if self.force_next:
            L = int(min(max(self.crossfade_s, 0.05), 3.0) * SR)
            L = max(1, min(L, c.remaining, len(p["pcm"]) // 2))
            self._start_pending(L, L)
        elif xf > 0 and c.remaining <= xf:
            self._start_pending(xf, xf)
        elif xf <= 0 and c.remaining <= n:
            self._start_pending(0, max(c.remaining, 1))

    def render(self, n=BLOCK):
        out = np.zeros((n, 2), np.float32)
        with self.lock:
            if self.paused:
                self._meter(out)
                return out
            self._maybe_advance(n)

            music = np.zeros((n, 2), np.float32)
            voices = ([self.cur] if self.cur is not None else []) + self.fading
            for v in voices:
                ch = v.read(n)
                if ch is not None:
                    music[:len(ch)] += ch
            self.fading = [v for v in self.fading if not v.done]
            if self.cur is not None and self.cur.done:
                self.cur = None
                if self.pending is None:
                    self.events.put(("idle",))
                    self.events.put(("need_next",))

            cart = np.zeros((n, 2), np.float32)
            ducking = bool(self.carts)
            for v in self.carts:
                ch = v.read(n)
                if ch is not None:
                    cart[:len(ch)] += ch
            self.carts = [v for v in self.carts if not v.done]

            target = self.duck_level if ducking else 1.0
            step = n / (0.25 * SR)
            new = self.duck_gain + max(-step, min(step, target - self.duck_gain))
            gains = np.linspace(self.duck_gain, new, n, dtype=np.float32)
            self.duck_gain = new
            out = (music * gains[:, None] + cart) * np.float32(self.master)
            np.clip(out, -1.0, 1.0, out=out)
            self._meter(out)
        tap = self.tap
        if tap is not None:
            try:
                tap(out)
            except Exception:
                pass
        return out

    def _meter(self, out):
        self.peak = (float(np.abs(out[:, 0]).max()), float(np.abs(out[:, 1]).max())) if len(out) else (0.0, 0.0)

    # ---------------- output drivers ----------------
    def start(self, device=None):
        self._alive = True
        try:
            import sounddevice as sd
            self._stream = sd.OutputStream(samplerate=SR, channels=2, dtype="float32",
                                           blocksize=BLOCK, device=device, callback=self._cb)
            self._stream.start()
            self.driver = "device"
        except Exception as e:  # no sound card, bad device, missing PortAudio...
            self.driver_error = str(e)
            self._start_clock()
        self.events.put(("need_next",))

    def _cb(self, outdata, frames, time_info, status):
        outdata[:] = self.render(frames)

    def _start_clock(self):
        self.driver = "clock"

        def run():
            nxt = time.monotonic()
            while self._alive:
                self.render(BLOCK)
                nxt += BLOCK / SR
                delay = nxt - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                else:
                    nxt = time.monotonic()
        self._clock = threading.Thread(target=run, daemon=True)
        self._clock.start()

    def shutdown(self):
        self._alive = False
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception:
                pass
            self._stream = None
