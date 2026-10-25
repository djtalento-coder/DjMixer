# AutoFade Radio

Radio automation for Windows in the spirit of OtsAV: set it up once and it runs your station.

- **Crossfading playlist** - equal-power crossfade (0-12 s), silence auto-trimmed from track
  starts/ends so fades never waste time on dead air, loudness leveling between tracks
- **Auto-DJ** - when the queue runs dry it plays random songs from your rotation folders
  (avoids recent repeats), so the station never goes silent
- **Jingles/sweepers** - automatically inserted every N songs from a jingle folder
- **Cart wall** - 16 one-click pads (left-click play, right-click assign); music ducks under them
- **Icecast streaming** - MP3 encode to your Icecast server, sends Artist - Title metadata
- **Now-playing text file** for overlays/websites, and a **play log (CSV)** with timestamps
  at `%APPDATA%\AutoFadeRadio\played_log.csv` for royalty reporting
- Reads MP3, FLAC, WAV, OGG, M4A/AAC, WMA, Opus (ffmpeg is bundled)

## Get the .exe (no Python needed)
Push this folder to a GitHub repo. The workflow in `.github/workflows/build-exe.yml` builds
`AutoFadeRadio.exe` on every push (Actions tab > latest run > Artifacts). Download, unzip, run.

## Run from source
```
pip install -r requirements.txt
python app.py
```
(needs Python 3.10+ with Tkinter, which the python.org installer includes)

## Use it
1. Station settings: add rotation folders, a jingle folder, optional now-playing file.
2. Add songs to the queue (or rely on auto-fill), press START.
3. To broadcast: fill in your Icecast host/port/mount/password and press Go live.

Tests (engine + automation): `python tests/test_engine.py` and `python tests/test_controller.py`.

Note: playing licensed music on a public stream needs the right licenses (e.g. SoundExchange
in the US); this app doesn't handle licensing.
