"""Automation brain: keeps the next track decoded and queued, inserts jingles,
auto-fills from rotation folders, logs plays, writes now-playing, drives the stream."""
import csv
import os
import queue
import random
import threading
import time
from collections import deque

from . import decode
from .config import app_dir
from .engine import Engine
from .stream import StreamOut


def scan_folder(folder):
    found = []
    for root, _dirs, files in os.walk(folder):
        for f in files:
            if decode.is_audio(f):
                found.append(os.path.join(root, f))
    return found


class Controller:
    def __init__(self, engine: Engine, cfg):
        self.engine = engine
        self.cfg = cfg
        self.playlist = [p for p in cfg["playlist"] if os.path.exists(p)]
        self.ui = queue.SimpleQueue()          # ('now', info) ('playlist',) ('error', msg)
        self.running = False
        self.since_jingle = 0
        self.recent = deque(maxlen=60)
        self.rotation = []
        self.jingles = []
        self.stream = StreamOut()
        self.engine.tap = self.stream.push
        self.lock = threading.RLock()
        self._stop = threading.Event()
        self._cart_cache = {}
        self.log_path = os.path.join(app_dir(), "played_log.csv")
        self.apply_config()
        self.refresh_libraries()
        self.thread = threading.Thread(target=self._loop, daemon=True)
        self.thread.start()

    # ------------- config / libraries -------------
    def apply_config(self):
        self.engine.crossfade_s = float(self.cfg["crossfade_s"])
        self.engine.duck_level = float(self.cfg["duck_level"])

    def refresh_libraries(self):
        def work():
            rot = []
            for d in self.cfg["rotation_folders"]:
                if os.path.isdir(d):
                    rot += scan_folder(d)
            jf = self.cfg["jingle_folder"]
            jin = scan_folder(jf) if jf and os.path.isdir(jf) else []
            with self.lock:
                self.rotation, self.jingles = rot, jin
        threading.Thread(target=work, daemon=True).start()

    # ------------- playlist (called from UI thread) -------------
    def _save_playlist(self):
        self.cfg["playlist"] = list(self.playlist)
        self.cfg.save()
        self.ui.put(("playlist",))

    def add(self, paths):
        with self.lock:
            self.playlist += [p for p in paths if decode.is_audio(p)]
        self._save_playlist()

    def remove(self, i):
        with self.lock:
            if 0 <= i < len(self.playlist):
                del self.playlist[i]
        self._save_playlist()

    def move(self, i, j):
        with self.lock:
            if 0 <= i < len(self.playlist) and 0 <= j < len(self.playlist):
                self.playlist.insert(j, self.playlist.pop(i))
        self._save_playlist()

    def clear(self):
        with self.lock:
            self.playlist.clear()
        self._save_playlist()

    # ------------- transport -------------
    def start(self):
        self.running = True
        self.engine.accepting = True
        self.engine.paused = False
        self.engine.events.put(("need_next",))

    def stop(self):
        self.running = False
        self.engine.stop_all(1.5)

    def skip(self):
        if not self.running:
            self.start()
        self.engine.skip()

    def shutdown(self):
        self._stop.set()
        self.engine.stop_all(0.05)
        self.stream.stop()
        self.cfg.save()

    # ------------- carts -------------
    def play_cart(self, i):
        path = self.cfg["carts"][i]
        if not path or not os.path.exists(path):
            return

        def work():
            try:
                pcm = self._cart_cache.get(path)
                if pcm is None:
                    pcm = decode.decode(path, trim=True, level=True)
                    if len(self._cart_cache) > 24:
                        self._cart_cache.clear()
                    self._cart_cache[path] = pcm
                self.engine.play_cart(pcm, {"path": path})
            except decode.DecodeError as e:
                self.ui.put(("error", f"Cart {i + 1}: {e}"))
        threading.Thread(target=work, daemon=True).start()

    # ------------- streaming -------------
    def start_stream(self):
        ok = self.stream.start(self.cfg["stream"])
        self.cfg.save()
        return ok

    def stop_stream(self):
        self.stream.stop()

    # ------------- worker -------------
    def _loop(self):
        while not self._stop.is_set():
            try:
                ev = self.engine.events.get(timeout=1.0)
            except queue.Empty:
                ev = None
            if ev and ev[0] == "started":
                self._on_started(ev[1])
            if self.running and not self.engine.has_pending:
                self._fill()

    def _pick(self):
        with self.lock:
            every = int(self.cfg["jingle_every"])
            if every > 0 and self.jingles and self.since_jingle >= every:
                self.since_jingle = 0
                return random.choice(self.jingles), "jingle"
            if self.playlist:
                p = self.playlist.pop(0)
                self.since_jingle += 1
                self._save_playlist()
                return p, "song"
            if self.cfg["autofill"] and self.rotation:
                avoid = set(self.recent)
                pool = [p for p in self.rotation if p not in avoid] or self.rotation
                self.since_jingle += 1
                return random.choice(pool), "song"
        return None

    def _fill(self):
        for _ in range(6):
            pick = self._pick()
            if pick is None:
                return
            path, kind = pick
            try:
                pcm = decode.decode(path, trim=True, level=bool(self.cfg["normalize"]))
            except decode.DecodeError as e:
                self.ui.put(("error", f"Skipped {os.path.basename(path)}: {e}"))
                continue
            artist, title = decode.read_tags(path)
            info = {"path": path, "artist": artist, "title": title, "kind": kind,
                    "duration": len(pcm) / 44100}
            self.recent.append(path)
            self.engine.set_pending(pcm, info)
            return

    def _on_started(self, info):
        song = f"{info['artist']} - {info['title']}" if info["artist"] else info["title"]
        npf = self.cfg["nowplaying_file"]
        if npf:
            try:
                with open(npf, "w", encoding="utf-8") as f:
                    f.write(song)
            except OSError:
                pass
        try:
            new = not os.path.exists(self.log_path)
            with open(self.log_path, "a", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                if new:
                    w.writerow(["timestamp", "kind", "artist", "title", "file"])
                w.writerow([time.strftime("%Y-%m-%d %H:%M:%S"), info["kind"], info["artist"],
                            info["title"], info["path"]])
        except OSError:
            pass
        self.ui.put(("now", info))
        if info["kind"] == "song":
            threading.Thread(target=self.stream.update_metadata, args=(song,), daemon=True).start()
