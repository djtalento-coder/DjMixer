import os, subprocess, sys, tempfile, time, queue
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from radio import decode
from radio.config import Config
from radio.engine import Engine
from radio.controller import Controller

tmp = tempfile.mkdtemp()
os.environ["APPDATA"] = tmp
music = os.path.join(tmp, "music"); jin = os.path.join(tmp, "jingles")
os.makedirs(music); os.makedirs(jin)
def tone(path, f, s):
    subprocess.run([decode.FFMPEG, "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={f}:duration={s}", "-metadata", f"title=T{f}", "-metadata", "artist=Art", path], check=True)
for f in (300, 400, 500): tone(os.path.join(music, f"s{f}.mp3"), f, 4)
tone(os.path.join(jin, "j.mp3"), 900, 1.5)

cfg = Config(os.path.join(tmp, "c.json"))
cfg["crossfade_s"] = 1.0; cfg["rotation_folders"] = [music]; cfg["jingle_folder"] = jin
cfg["jingle_every"] = 2; cfg["nowplaying_file"] = os.path.join(tmp, "np.txt")
e = Engine(); c = Controller(e, cfg); e.start(); time.sleep(1.0)   # no sound device here -> clock driver
print("driver:", e.driver)
c.start()
seen = []; t0 = time.time()
while time.time() - t0 < 14:
    try: ev = c.ui.get(timeout=0.5)
    except queue.Empty: continue
    if ev[0] == "now": seen.append((ev[1]["kind"], ev[1]["title"])); print(round(time.time()-t0,1), ev[1]["kind"], ev[1]["artist"], ev[1]["title"])
    elif ev[0] == "error": print("ERR", ev[1])
kinds = [k for k, _ in seen]
assert len(seen) >= 4, seen
assert "jingle" in kinds, kinds
assert open(cfg["nowplaying_file"]).read()
print(open(c.log_path).read().splitlines()[:3])
c.stop(); time.sleep(2.0); assert e.state()["info"] is None
c.shutdown(); print("CONTROLLER OK")
