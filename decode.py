"""Decode any audio file to float32 stereo 44.1 kHz using a bundled ffmpeg."""
import os
import subprocess

import numpy as np
import imageio_ffmpeg

SR = 44100
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
AUDIO_EXTS = {".mp3", ".wav", ".flac", ".ogg", ".m4a", ".aac", ".wma", ".opus", ".aiff"}
_NOWIN = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW


class DecodeError(Exception):
    pass


def is_audio(path):
    return os.path.splitext(path)[1].lower() in AUDIO_EXTS


def trim_silence(pcm, thresh_db=-50.0):
    """Cut leading/trailing near-silence so fades don't get wasted on dead air."""
    thr = 10 ** (thresh_db / 20)
    amp = np.abs(pcm).max(axis=1)
    idx = np.nonzero(amp > thr)[0]
    if len(idx) == 0:
        return pcm
    start = max(int(idx[0]) - int(0.01 * SR), 0)
    end = min(int(idx[-1]) + int(0.05 * SR), len(pcm))
    return pcm[start:end]


def normalize(pcm, target_db=-18.0):
    """Gentle loudness leveling so tracks hit the air at similar volume."""
    if len(pcm) == 0:
        return pcm
    rms = float(np.sqrt(np.mean(pcm.astype(np.float64) ** 2)))
    peak = float(np.abs(pcm).max())
    if rms < 1e-5 or peak < 1e-5:
        return pcm
    gain = (10 ** (target_db / 20)) / rms
    gain = min(max(gain, 0.25), 4.0, 0.98 / peak)
    return (pcm * np.float32(gain)).astype(np.float32)


def decode(path, trim=True, level=True):
    cmd = [FFMPEG, "-v", "error", "-i", path, "-vn", "-f", "f32le",
           "-acodec", "pcm_f32le", "-ar", str(SR), "-ac", "2", "-"]
    try:
        p = subprocess.run(cmd, capture_output=True, creationflags=_NOWIN)
    except OSError as e:
        raise DecodeError(str(e))
    buf = p.stdout[: len(p.stdout) // 8 * 8]
    if not buf:
        raise DecodeError(p.stderr.decode(errors="ignore")[:200] or "no audio")
    pcm = np.frombuffer(buf, dtype=np.float32).reshape(-1, 2).copy()
    if trim:
        pcm = trim_silence(pcm)
    if level:
        pcm = normalize(pcm)
    if len(pcm) < SR // 2:
        raise DecodeError("track too short")
    return pcm


def read_tags(path):
    """Return (artist, title) from file tags, falling back to the file name."""
    artist = title = ""
    try:
        p = subprocess.run([FFMPEG, "-v", "error", "-i", path, "-f", "ffmetadata", "-"],
                           capture_output=True, creationflags=_NOWIN, timeout=20)
        for line in p.stdout.decode("utf-8", errors="ignore").splitlines():
            if "=" in line and not line.startswith((";", "#")):
                k, v = line.split("=", 1)
                k = k.strip().lower()
                if k == "title" and not title:
                    title = v.strip()
                elif k == "artist" and not artist:
                    artist = v.strip()
    except Exception:
        pass
    if not title:
        title = os.path.splitext(os.path.basename(path))[0]
    return artist, title
