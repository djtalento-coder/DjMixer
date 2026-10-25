import copy
import json
import os

DEFAULTS = {
    "crossfade_s": 6.0,
    "normalize": True,
    "duck_level": 0.35,
    "autofill": True,
    "rotation_folders": [],
    "jingle_folder": "",
    "jingle_every": 4,
    "nowplaying_file": "",
    "device": None,
    "stream": {
        "host": "localhost",
        "port": 8000,
        "mount": "live",
        "user": "source",
        "password": "",
        "bitrate": 128,
    },
    "carts": [None] * 16,
    "playlist": [],
}


def app_dir():
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "AutoFadeRadio")
    os.makedirs(d, exist_ok=True)
    return d


def _merge(base, over):
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


class Config:
    def __init__(self, path=None):
        self.path = path or os.path.join(app_dir(), "config.json")
        self.data = copy.deepcopy(DEFAULTS)
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                _merge(self.data, json.load(f))
        except (OSError, ValueError):
            pass
        while len(self.data["carts"]) < 16:
            self.data["carts"].append(None)

    def save(self):
        try:
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2)
        except OSError:
            pass

    def __getitem__(self, k):
        return self.data[k]

    def __setitem__(self, k, v):
        self.data[k] = v
