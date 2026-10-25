"""Optional Icecast source client: pipes the mixed output through ffmpeg (MP3) to a server."""
import queue
import subprocess
import threading
import urllib.parse
import urllib.request
import base64

from .decode import FFMPEG, _NOWIN


class StreamOut:
    def __init__(self):
        self.proc = None
        self.q = queue.Queue(maxsize=400)
        self.thread = None
        self.status = "off"
        self.cfg = None

    @property
    def active(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self, cfg):
        self.stop()
        self.cfg = dict(cfg)
        user = urllib.parse.quote(cfg.get("user") or "source", safe="")
        pw = urllib.parse.quote(cfg.get("password") or "", safe="")
        mount = (cfg.get("mount") or "live").lstrip("/")
        url = f"icecast://{user}:{pw}@{cfg['host']}:{int(cfg['port'])}/{mount}"
        cmd = [FFMPEG, "-loglevel", "error", "-f", "f32le", "-ar", "44100", "-ac", "2",
               "-i", "-", "-c:a", "libmp3lame", "-b:a", f"{int(cfg.get('bitrate', 128))}k",
               "-content_type", "audio/mpeg", "-f", "mp3", url]
        try:
            self.proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE,
                                         creationflags=_NOWIN)
        except OSError as e:
            self.status = f"error: {e}"
            return False
        self.status = "connecting"
        self.thread = threading.Thread(target=self._writer, daemon=True)
        self.thread.start()
        return True

    def _writer(self):
        proc = self.proc
        try:
            while proc is self.proc and proc.poll() is None:
                try:
                    data = self.q.get(timeout=0.5)
                except queue.Empty:
                    continue
                proc.stdin.write(data)
                proc.stdin.flush()
                self.status = "live"
        except (BrokenPipeError, OSError, ValueError):
            pass
        if proc is self.proc:
            err = ""
            try:
                err = proc.stderr.read().decode(errors="ignore").strip().splitlines()[-1]
            except Exception:
                pass
            self.status = "error: " + (err or "connection closed")

    def push(self, block):
        if self.proc is None:
            return
        try:
            self.q.put_nowait(block.tobytes())
        except queue.Full:
            pass  # never block the audio thread

    def stop(self):
        proc, self.proc = self.proc, None
        if proc is not None:
            try:
                proc.stdin.close()
            except Exception:
                pass
            try:
                proc.terminate()
            except Exception:
                pass
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break
        self.status = "off"

    def update_metadata(self, song):
        """Push 'Artist - Title' to Icecast's admin metadata endpoint (best effort)."""
        c = self.cfg
        if not self.active or not c:
            return
        mount = "/" + (c.get("mount") or "live").lstrip("/")
        q = urllib.parse.urlencode({"mode": "updinfo", "mount": mount, "song": song})
        req = urllib.request.Request(f"http://{c['host']}:{int(c['port'])}/admin/metadata?{q}")
        tok = base64.b64encode(f"{c.get('user') or 'source'}:{c.get('password') or ''}".encode()).decode()
        req.add_header("Authorization", "Basic " + tok)
        try:
            urllib.request.urlopen(req, timeout=5).close()
        except Exception:
            pass
