import os, subprocess, sys, tempfile, time
import numpy as np
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from radio import decode
from radio.engine import Engine, SR

tmp = tempfile.mkdtemp()

def tone(name, freq, secs, lead=0.0):
    p = os.path.join(tmp, name)
    af = f"adelay={int(lead*1000)}|{int(lead*1000)}" if lead else "anull"
    subprocess.run([decode.FFMPEG, "-v", "error", "-f", "lavfi", "-i",
                    f"sine=frequency={freq}:duration={secs}:sample_rate=44100", "-af", af, "-ac", "2", p], check=True)
    return p

def render_all(e, seconds):
    blocks = []
    for _ in range(int(seconds * SR / 1024)):
        blocks.append(e.render(1024))
    return np.concatenate(blocks)

# --- decode, trim & normalize
a = tone("a.wav", 440, 10, lead=1.0)         # 1s silence in front
pa = decode.decode(a, level=False)
assert abs(len(pa) / SR - 10.0) < 0.15, len(pa) / SR   # leading silence trimmed (10s tone kept)
pn = decode.decode(a, level=True)
assert np.abs(pn).max() <= 0.981

# --- crossfade timing + equal-power
b = tone("b.wav", 880, 10)
pb = decode.decode(b, level=False)
e = Engine(); e.crossfade_s = 3.0
e.set_pending(pa, {"title": "A"}); e.render(1024)       # starts A
e.set_pending(pb, {"title": "B"})
out = render_all(e, 19)
started = [ev for ev in iter(lambda: e.events.get_nowait() if not e.events.empty() else None, None) if ev[0] == "started"]
assert [s[1]["title"] for s in started] == ["A", "B"], started
total = len(pa) + len(pb) - 3 * SR
nz = np.nonzero(np.abs(out[:, 0]) > 1e-4)[0]
dur = (nz[-1] - nz[0]) / SR
print("program length", round(dur, 2), "expected", round(total / SR, 2))
assert abs(dur - total / SR) < 0.1
# level through the crossfade region must stay ~constant (no dip to silence)
mid = out[int((len(pa) - 2.5 * SR)):int((len(pa) - 0.5 * SR)), 0]
rms = [np.sqrt(np.mean(mid[i:i + 4410] ** 2)) for i in range(0, len(mid) - 4410, 4410)]
print("rms through fade", [round(r, 3) for r in rms])
assert min(rms) > 0.4 * max(rms)

# --- crossfade 0 => gapless, no long silence
e = Engine(); e.crossfade_s = 0.0
e.set_pending(pa, {"title": "A"}); e.render(1024); e.set_pending(pb, {"title": "B"})
out = render_all(e, 21)
gap = np.abs(out[:, 0]) < 1e-4
nz = np.nonzero(~gap)[0]
body = gap[nz[0]:nz[-1]]
longest = max((len(list(g)) for k, g in __import__("itertools").groupby(body) if k), default=0)
print("longest zero run (samples) at xfade=0:", longest)
assert longest < 2000

# --- cart ducking
e = Engine(); e.set_pending(pa, {"title": "A"}); render_all(e, 1)
e.play_cart(pb[:SR * 2])
render_all(e, 1)
assert e.duck_gain < 0.5, e.duck_gain
render_all(e, 3)
assert e.duck_gain > 0.99, e.duck_gain

# --- skip forces a short crossfade
e = Engine(); e.crossfade_s = 6.0
e.set_pending(pa, {"title": "A"}); render_all(e, 1); e.set_pending(pb, {"title": "B"}); e.skip(); render_all(e, 0.1)
assert e.cur.info["title"] == "B"
print("ENGINE OK")
