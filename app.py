"""AutoFade Radio - radio automation for Windows (OtsAV-style): crossfading playlist,
auto-DJ rotation, jingle inserts, cart wall with ducking, optional Icecast streaming."""
import os
import queue
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from radio import decode
from radio.config import Config
from radio.controller import Controller
from radio.engine import Engine

BG = "#15171c"
PANEL = "#1e2128"
FG = "#e8e8e8"
DIM = "#8b93a1"
ACCENT = "#ff9800"
GREEN = "#43c463"


def fmt(sec):
    sec = max(0, int(sec))
    return f"{sec // 60}:{sec % 60:02d}"


class App:
    def __init__(self, root):
        self.root = root
        root.title("AutoFade Radio")
        root.geometry("1000x700")
        root.minsize(900, 620)
        root.configure(bg=BG)

        self.cfg = Config()
        self.engine = Engine()
        self.ctl = Controller(self.engine, self.cfg)
        self.engine.start(self.cfg["device"])

        self._style()
        self._build()
        self._refresh_playlist()
        root.protocol("WM_DELETE_WINDOW", self.on_close)
        self.poll()

        if self.engine.driver == "clock":
            self.set_status("No audio device found - running without local monitoring ("
                            + self.engine.driver_error[:60] + ")")

    # ------------------------------------------------------------ style/layout
    def _style(self):
        st = ttk.Style()
        st.theme_use("clam")
        st.configure(".", background=BG, foreground=FG, fieldbackground=PANEL, bordercolor=PANEL)
        st.configure("TLabel", background=BG, foreground=FG)
        st.configure("TFrame", background=BG)
        st.configure("Panel.TFrame", background=PANEL)
        st.configure("TLabelframe", background=BG, foreground=DIM)
        st.configure("TLabelframe.Label", background=BG, foreground=DIM)
        st.configure("TButton", background=PANEL, foreground=FG, padding=6)
        st.map("TButton", background=[("active", "#2b303a")])
        st.configure("Go.TButton", background=GREEN, foreground="black", font=("Segoe UI", 11, "bold"))
        st.map("Go.TButton", background=[("active", "#5fd67c")])
        st.configure("Stop.TButton", background="#c0392b", foreground="white", font=("Segoe UI", 11, "bold"))
        st.configure("Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL)
        st.configure("TCheckbutton", background=BG, foreground=FG)
        st.configure("TEntry", insertcolor=FG)

    def _build(self):
        r = self.root
        # ---- now playing
        top = ttk.Frame(r, padding=(14, 12, 14, 4))
        top.pack(fill="x")
        self.now_title = ttk.Label(top, text="Off air", font=("Segoe UI", 20, "bold"))
        self.now_title.pack(anchor="w")
        self.now_sub = ttk.Label(top, text="Press START to begin automation", foreground=DIM)
        self.now_sub.pack(anchor="w")
        bar = ttk.Frame(top)
        bar.pack(fill="x", pady=(8, 0))
        self.progress = ttk.Progressbar(bar, maximum=1000)
        self.progress.pack(side="left", fill="x", expand=True)
        self.time_lbl = ttk.Label(bar, text="0:00 / 0:00", width=14, anchor="e")
        self.time_lbl.pack(side="left", padx=(8, 0))
        self.vu = tk.Canvas(top, height=14, bg=PANEL, highlightthickness=0)
        self.vu.pack(fill="x", pady=(8, 0))

        # ---- transport
        tr = ttk.Frame(r, padding=(14, 8))
        tr.pack(fill="x")
        self.start_btn = ttk.Button(tr, text="START", style="Go.TButton", command=self.on_start)
        self.start_btn.pack(side="left")
        ttk.Button(tr, text="STOP", style="Stop.TButton", command=self.ctl.stop).pack(side="left", padx=6)
        ttk.Button(tr, text="Skip >>", command=self.ctl.skip).pack(side="left", padx=6)
        ttk.Label(tr, text="Crossfade").pack(side="left", padx=(24, 6))
        self.xf = tk.DoubleVar(value=self.cfg["crossfade_s"])
        ttk.Scale(tr, from_=0, to=12, variable=self.xf, length=180, command=self.on_xf).pack(side="left")
        self.xf_lbl = ttk.Label(tr, width=8)
        self.xf_lbl.pack(side="left", padx=6)
        self.on_xf()
        ttk.Button(tr, text="Station settings...", command=self.open_settings).pack(side="right")

        # ---- middle: playlist + carts
        mid = ttk.Frame(r, padding=(14, 4))
        mid.pack(fill="both", expand=True)
        left = ttk.LabelFrame(mid, text="Up next (queue)", padding=8)
        left.pack(side="left", fill="both", expand=True)
        self.lb = tk.Listbox(left, bg=PANEL, fg=FG, selectbackground=ACCENT, selectforeground="black",
                             highlightthickness=0, borderwidth=0, activestyle="none",
                             selectmode="extended", font=("Segoe UI", 10))
        sb = ttk.Scrollbar(left, command=self.lb.yview)
        self.lb.config(yscrollcommand=sb.set)
        self.lb.pack(side="left", fill="both", expand=True)
        sb.pack(side="left", fill="y")
        btns = ttk.Frame(left)
        btns.pack(side="left", fill="y", padx=(8, 0))
        for text, cmd in [("Add files...", self.add_files), ("Add folder...", self.add_folder),
                          ("Move up", lambda: self.move(-1)), ("Move down", lambda: self.move(1)),
                          ("Remove", self.remove_sel), ("Clear", self.ctl.clear)]:
            ttk.Button(btns, text=text, command=cmd).pack(fill="x", pady=2)
        self.autofill_var = tk.BooleanVar(value=self.cfg["autofill"])
        ttk.Checkbutton(btns, text="Auto-fill from\nrotation folders", variable=self.autofill_var,
                        command=self.on_autofill).pack(pady=(10, 0))

        right = ttk.LabelFrame(mid, text="Cart wall (click = play, right-click = assign)", padding=8)
        right.pack(side="left", fill="y", padx=(10, 0))
        self.cart_btns = []
        for i in range(16):
            b = tk.Button(right, width=12, height=3, bg=PANEL, fg=FG, activebackground=ACCENT,
                          relief="flat", wraplength=90, font=("Segoe UI", 8),
                          command=lambda i=i: self.ctl.play_cart(i))
            b.grid(row=i // 4, column=i % 4, padx=3, pady=3)
            b.bind("<Button-3>", lambda e, i=i: self.assign_cart(i))
            self.cart_btns.append(b)
        self._refresh_carts()

        # ---- stream
        st = ttk.LabelFrame(r, text="Internet stream (Icecast)", padding=8)
        st.pack(fill="x", padx=14, pady=(6, 4))
        s = self.cfg["stream"]
        self.sv = {k: tk.StringVar(value=str(s[k])) for k in ("host", "port", "mount", "user", "password", "bitrate")}
        for col, (label, key, w, show) in enumerate([
                ("Host", "host", 18, ""), ("Port", "port", 6, ""), ("Mount", "mount", 10, ""),
                ("User", "user", 8, ""), ("Password", "password", 12, "*"), ("kbps", "bitrate", 5, "")]):
            ttk.Label(st, text=label).grid(row=0, column=col * 2, padx=(6, 2))
            ttk.Entry(st, textvariable=self.sv[key], width=w, show=show).grid(row=0, column=col * 2 + 1)
        self.stream_btn = ttk.Button(st, text="Go live", command=self.toggle_stream)
        self.stream_btn.grid(row=0, column=12, padx=10)
        self.stream_lbl = ttk.Label(st, text="off", foreground=DIM, width=28)
        self.stream_lbl.grid(row=0, column=13)

        self.status = ttk.Label(r, text="", foreground=DIM, padding=(14, 0, 14, 8))
        self.status.pack(fill="x")

    # ------------------------------------------------------------ actions
    def set_status(self, text):
        self.status.config(text=text)

    def on_start(self):
        if not self.ctl.playlist and not (self.cfg["autofill"] and self.cfg["rotation_folders"]):
            messagebox.showinfo("AutoFade Radio",
                                "The queue is empty. Add songs, or set rotation folders in "
                                "Station settings and enable auto-fill.")
            return
        self.ctl.start()

    def on_xf(self, *_):
        v = round(self.xf.get())
        self.cfg["crossfade_s"] = float(v)
        self.engine.crossfade_s = float(v)
        self.xf_lbl.config(text="off" if v == 0 else f"{v} s")

    def on_autofill(self):
        self.cfg["autofill"] = bool(self.autofill_var.get())
        self.cfg.save()

    def add_files(self):
        exts = " ".join("*" + e for e in sorted(decode.AUDIO_EXTS))
        paths = filedialog.askopenfilenames(title="Add songs",
                                            filetypes=[("Audio files", exts), ("All files", "*.*")])
        if paths:
            self.ctl.add(list(paths))

    def add_folder(self):
        d = filedialog.askdirectory(title="Add every song in a folder")
        if d:
            from radio.controller import scan_folder
            self.ctl.add(sorted(scan_folder(d)))

    def remove_sel(self):
        for i in reversed(self.lb.curselection()):
            self.ctl.remove(i)

    def move(self, d):
        sel = self.lb.curselection()
        if len(sel) == 1:
            i = sel[0]
            self.ctl.move(i, i + d)
            self.lb.selection_clear(0, "end")
            j = max(0, min(i + d, self.lb.size() - 1))
            self.lb.selection_set(j)

    def _refresh_playlist(self):
        self.lb.delete(0, "end")
        for p in self.ctl.playlist:
            self.lb.insert("end", os.path.splitext(os.path.basename(p))[0])

    def _refresh_carts(self):
        for i, b in enumerate(self.cart_btns):
            p = self.cfg["carts"][i]
            b.config(text=os.path.splitext(os.path.basename(p))[0] if p else f"Cart {i + 1}\n(empty)",
                     fg=FG if p else DIM)

    def assign_cart(self, i):
        p = filedialog.askopenfilename(title=f"Assign cart {i + 1}",
                                       filetypes=[("Audio files", " ".join("*" + e for e in sorted(decode.AUDIO_EXTS)))])
        if p:
            self.cfg["carts"][i] = p
            self.cfg.save()
            self._refresh_carts()

    def toggle_stream(self):
        if self.ctl.stream.proc is not None:
            self.ctl.stop_stream()
            self.stream_btn.config(text="Go live")
            return
        try:
            s = self.cfg["stream"]
            s["host"] = self.sv["host"].get().strip()
            s["port"] = int(self.sv["port"].get())
            s["mount"] = self.sv["mount"].get().strip()
            s["user"] = self.sv["user"].get().strip()
            s["password"] = self.sv["password"].get()
            s["bitrate"] = int(self.sv["bitrate"].get())
        except ValueError:
            messagebox.showerror("Stream", "Port and kbps must be numbers.")
            return
        if self.ctl.start_stream():
            self.stream_btn.config(text="Stop stream")

    # ------------------------------------------------------------ settings dialog
    def open_settings(self):
        w = tk.Toplevel(self.root)
        w.title("Station settings")
        w.configure(bg=BG)
        w.geometry("620x520")
        w.transient(self.root)
        pad = {"padx": 12, "pady": 4}

        ttk.Label(w, text="Rotation folders (auto-DJ plays random songs from these when the queue is empty)",
                  wraplength=580).pack(anchor="w", **pad)
        fr = ttk.Frame(w)
        fr.pack(fill="x", **pad)
        rot = tk.Listbox(fr, height=5, bg=PANEL, fg=FG, highlightthickness=0, borderwidth=0)
        rot.pack(side="left", fill="x", expand=True)
        for d in self.cfg["rotation_folders"]:
            rot.insert("end", d)
        rb = ttk.Frame(fr)
        rb.pack(side="left", padx=6)

        def add_rot():
            d = filedialog.askdirectory(parent=w)
            if d:
                rot.insert("end", d)

        def del_rot():
            for i in reversed(rot.curselection()):
                rot.delete(i)
        ttk.Button(rb, text="Add...", command=add_rot).pack(fill="x", pady=2)
        ttk.Button(rb, text="Remove", command=del_rot).pack(fill="x", pady=2)

        ttk.Label(w, text="Jingle / sweeper folder").pack(anchor="w", **pad)
        jf = tk.StringVar(value=self.cfg["jingle_folder"])
        row = ttk.Frame(w)
        row.pack(fill="x", **pad)
        ttk.Entry(row, textvariable=jf).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse...", command=lambda: jf.set(filedialog.askdirectory(parent=w) or jf.get())).pack(side="left", padx=6)
        je = tk.StringVar(value=str(self.cfg["jingle_every"]))
        row = ttk.Frame(w)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Play a jingle every").pack(side="left")
        ttk.Entry(row, textvariable=je, width=4).pack(side="left", padx=6)
        ttk.Label(row, text="songs (0 = never)").pack(side="left")

        ttk.Label(w, text="Now-playing text file (for stream overlays / website; blank = off)").pack(anchor="w", **pad)
        npf = tk.StringVar(value=self.cfg["nowplaying_file"])
        row = ttk.Frame(w)
        row.pack(fill="x", **pad)
        ttk.Entry(row, textvariable=npf).pack(side="left", fill="x", expand=True)
        ttk.Button(row, text="Browse...", command=lambda: npf.set(
            filedialog.asksaveasfilename(parent=w, defaultextension=".txt") or npf.get())).pack(side="left", padx=6)

        norm = tk.BooleanVar(value=self.cfg["normalize"])
        ttk.Checkbutton(w, text="Level-match tracks (volume normalization)", variable=norm).pack(anchor="w", **pad)

        row = ttk.Frame(w)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="Music ducking under carts (0.1 = deep, 1 = none)").pack(side="left")
        duck = tk.StringVar(value=str(self.cfg["duck_level"]))
        ttk.Entry(row, textvariable=duck, width=5).pack(side="left", padx=6)

        ttk.Label(w, text="Output device").pack(anchor="w", **pad)
        devs = ["(system default)"]
        try:
            import sounddevice as sd
            devs += [f"{i}: {d['name']}" for i, d in enumerate(sd.query_devices()) if d["max_output_channels"] > 0]
        except Exception:
            pass
        cur = self.cfg["device"]
        dev = tk.StringVar(value=next((d for d in devs if cur is not None and d.startswith(f"{cur}:")), devs[0]))
        ttk.Combobox(w, textvariable=dev, values=devs, state="readonly").pack(fill="x", **pad)
        ttk.Label(w, text="(output device changes apply after restarting the program)", foreground=DIM).pack(anchor="w", padx=12)

        ttk.Label(w, text=f"Play log (for royalty reporting): {self.ctl.log_path}", foreground=DIM,
                  wraplength=580).pack(anchor="w", **pad)

        def save():
            try:
                every = int(je.get())
                dk = float(duck.get())
            except ValueError:
                messagebox.showerror("Settings", "Check the numeric fields.", parent=w)
                return
            self.cfg["rotation_folders"] = list(rot.get(0, "end"))
            self.cfg["jingle_folder"] = jf.get()
            self.cfg["jingle_every"] = every
            self.cfg["nowplaying_file"] = npf.get()
            self.cfg["normalize"] = bool(norm.get())
            self.cfg["duck_level"] = max(0.05, min(1.0, dk))
            self.cfg["device"] = None if dev.get().startswith("(") else int(dev.get().split(":")[0])
            self.cfg.save()
            self.ctl.apply_config()
            self.ctl.refresh_libraries()
            w.destroy()
        ttk.Button(w, text="Save", style="Go.TButton", command=save).pack(pady=10)

    # ------------------------------------------------------------ periodic UI update
    def poll(self):
        try:
            while True:
                ev = self.ctl.ui.get_nowait()
                if ev[0] == "now":
                    self.on_now(ev[1])
                elif ev[0] == "playlist":
                    self._refresh_playlist()
                elif ev[0] == "error":
                    self.set_status(ev[1])
        except queue.Empty:
            pass

        st = self.engine.state()
        if st["info"] is not None:
            self.progress["value"] = 1000 * st["pos_s"] / max(st["len_s"], 0.1)
            self.time_lbl.config(text=f"{fmt(st['pos_s'])} / {fmt(st['len_s'])}")
        else:
            self.progress["value"] = 0
            self.time_lbl.config(text="0:00 / 0:00")
            if not self.ctl.running:
                self.now_title.config(text="Off air")
        self.draw_vu()

        s = self.ctl.stream
        self.stream_lbl.config(text=s.status, foreground=GREEN if s.status == "live" else DIM)
        if s.proc is None:
            self.stream_btn.config(text="Go live")
        self.root.after(80, self.poll)

    def on_now(self, info):
        self.now_title.config(text=info["title"])
        bits = [info["artist"]] if info["artist"] else []
        if info["kind"] == "jingle":
            bits.append("(jingle)")
        self.now_sub.config(text="  ".join(bits) or " ")
        self._refresh_playlist()

    def draw_vu(self):
        c = self.vu
        c.delete("all")
        w = max(c.winfo_width(), 10)
        for k, level in enumerate(self.engine.peak):
            y0, y1 = k * 7, k * 7 + 6
            x = int(w * min(level, 1.0))
            c.create_rectangle(0, y0, x, y1, fill=GREEN if level < 0.8 else ("#e0b020" if level < 0.97 else "#d33"), width=0)

    def on_close(self):
        self.ctl.shutdown()
        self.engine.shutdown()
        self.root.destroy()


def main():
    root = tk.Tk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
