"""
pipeline_gui.py
---------------
CustomTkinter GUI launcher for the Texture Library Pipeline.

Usage:
    python pipeline_gui.py

Requirements:
    pip install customtkinter
"""

import json
import queue
import subprocess
import sys
import threading
from pathlib import Path
from tkinter import filedialog

import customtkinter as ctk

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

_HERE          = Path(__file__).parent.resolve()
_PIPELINE_DIR  = _HERE / "Texture Library Image Sorter" / "texture_pipeline"
_MAIN_PY       = _PIPELINE_DIR / "main.py"
_RESCAN_PY     = _PIPELINE_DIR / "rescan_library.py"
_PREVIEW_PY    = _HERE / "generate_preview.py"
_SETTINGS_FILE = _HERE / "gui_settings.json"

# ---------------------------------------------------------------------------
# Factory defaults
# ---------------------------------------------------------------------------

_FACTORY_PRESETS: dict[str, dict] = {
    "1": {
        "blank_stddev": 3.5,  "product_edge": 18.0, "line_art": 0.50,
        "tile_gradient": 1.40, "tile_seam": 15.0,   "tile_offset_seam": 1.25,
        "phash_hamming": 6,    "min_resolution": 768,
        "auto_bin": False,     "skip_checks": False,
    },
    "2": {
        "blank_stddev": 2.8,  "product_edge": 14.0, "line_art": 0.55,
        "tile_gradient": 1.60, "tile_seam": 20.0,   "tile_offset_seam": 1.35,
        "phash_hamming": 5,    "min_resolution": 640,
        "auto_bin": False,     "skip_checks": False,
    },
    "3": {
        "blank_stddev": 2.0,  "product_edge": 10.0, "line_art": 0.60,
        "tile_gradient": 1.80, "tile_seam": 25.0,   "tile_offset_seam": 1.50,
        "phash_hamming": 4,    "min_resolution": 512,
        "auto_bin": False,     "skip_checks": False,
    },
    "4": {
        "blank_stddev": 1.2,  "product_edge": 6.0,  "line_art": 0.72,
        "tile_gradient": 2.20, "tile_seam": 38.0,   "tile_offset_seam": 1.80,
        "phash_hamming": 4,    "min_resolution": 512,
        "auto_bin": True,      "skip_checks": False,
    },
    "5": {
        "blank_stddev": 0.5,  "product_edge": 2.0,  "line_art": 0.90,
        "tile_gradient": 1.80, "tile_seam": 25.0,   "tile_offset_seam": 1.50,
        "phash_hamming": 3,    "min_resolution": 256,
        "auto_bin": True,      "skip_checks": True,
    },
}

_FACTORY_SESSION: dict = {
    "input_dir": "", "output_dir": "",
    "ai_model": "gemma4:e4b", "cpu_workers": 6, "confidence": 3,
}

_LEVEL_LABELS: dict[int, str] = {
    1: "Level 1 — Low confidence: strict filters, many duplicates and failures expected",
    2: "Level 2 — Below average: tighter-than-default filters, some junk expected",
    3: "Level 3 — Default: balanced filters for a mixed-quality library",
    4: "Level 4 — High confidence: relaxed filters, auto-bins tileability failures",
    5: "Level 5 — Trusted source: skips quality checks entirely (verified seamless library)",
}


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class PipelineGUI(ctk.CTk):

    def __init__(self) -> None:
        super().__init__()

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("dark-blue")

        self.title("Texture Library Pipeline")
        self.geometry("980x800")
        self.minsize(820, 580)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Runtime state
        self._proc: subprocess.Popen | None = None
        self._log_queue: queue.Queue = queue.Queue()
        self._current_level: int = 3
        self._adv_visible: bool = False
        self._settings: dict = {}

        self._load_settings()
        self._build_ui()
        self._restore_session()

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------

    def _load_settings(self) -> None:
        """Read gui_settings.json, merging factory defaults for any missing keys."""
        raw: dict = {}
        if _SETTINGS_FILE.exists():
            try:
                raw = json.loads(_SETTINGS_FILE.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                pass

        session = {**_FACTORY_SESSION, **raw.get("last_session", {})}

        presets: dict = {}
        saved = raw.get("presets", {})
        for k, factory in _FACTORY_PRESETS.items():
            presets[k] = {**factory, **saved.get(k, {})}

        self._settings = {"last_session": session, "presets": presets}

    def _save_settings(self) -> None:
        """Flush current UI state into _settings, then write to disk."""
        self._capture_session()
        self._capture_preset()
        try:
            _SETTINGS_FILE.write_text(
                json.dumps(self._settings, indent=2), encoding="utf-8"
            )
        except OSError:
            pass

    def _capture_session(self) -> None:
        s = self._settings["last_session"]
        s["input_dir"]  = self._ent_input.get().strip()
        s["output_dir"] = self._ent_output.get().strip()
        s["ai_model"]   = self._ent_model.get().strip()
        try:
            s["cpu_workers"] = int(self._ent_workers.get())
        except ValueError:
            pass
        s["confidence"] = self._current_level

    def _capture_preset(self) -> None:
        """Read advanced field values into the current preset slot."""
        key = str(self._current_level)
        p   = self._settings["presets"][key]
        fac = _FACTORY_PRESETS[key]

        def _f(ent: ctk.CTkEntry, fallback: float) -> float:
            try:
                return float(ent.get())
            except ValueError:
                return fallback

        def _i(ent: ctk.CTkEntry, fallback: int) -> int:
            try:
                return int(ent.get())
            except ValueError:
                return fallback

        p["blank_stddev"]      = _f(self._ent_blank,       fac["blank_stddev"])
        p["product_edge"]      = _f(self._ent_edge,        fac["product_edge"])
        p["line_art"]          = _f(self._ent_lineart,     fac["line_art"])
        p["tile_gradient"]     = _f(self._ent_gradient,    fac["tile_gradient"])
        p["tile_seam"]         = _f(self._ent_seam,        fac["tile_seam"])
        p["tile_offset_seam"]  = _f(self._ent_offset_seam, fac["tile_offset_seam"])
        p["phash_hamming"]     = _i(self._ent_hamming,     fac["phash_hamming"])
        p["min_resolution"]    = _i(self._ent_minres,      fac["min_resolution"])
        p["auto_bin"]          = bool(self._var_autobin.get())
        p["skip_checks"]       = bool(self._var_skip.get())

    def _apply_preset(self, level: int) -> None:
        """Populate advanced fields from the saved preset for *level*."""
        p = self._settings["presets"][str(level)]

        def _set(ent: ctk.CTkEntry, value: object) -> None:
            ent.configure(state="normal")
            ent.delete(0, "end")
            ent.insert(0, str(value))

        _set(self._ent_blank,       p["blank_stddev"])
        _set(self._ent_edge,        p["product_edge"])
        _set(self._ent_lineart,     p["line_art"])
        _set(self._ent_gradient,    p["tile_gradient"])
        _set(self._ent_seam,        p["tile_seam"])
        _set(self._ent_offset_seam, p["tile_offset_seam"])
        _set(self._ent_hamming,     p["phash_hamming"])
        _set(self._ent_minres,      p["min_resolution"])

        self._var_autobin.set(1 if p["auto_bin"]    else 0)
        self._var_skip.set   (1 if p["skip_checks"] else 0)

        self._update_widget_states(level)

    def _restore_session(self) -> None:
        """Populate all fields from the last saved session on startup."""
        s = self._settings["last_session"]

        if s.get("input_dir"):
            self._ent_input.insert(0, s["input_dir"])
        if s.get("output_dir"):
            self._ent_output.insert(0, s["output_dir"])

        self._ent_model.delete(0, "end")
        self._ent_model.insert(0, s.get("ai_model", "gemma4:e4b"))
        self._ent_workers.delete(0, "end")
        self._ent_workers.insert(0, str(s.get("cpu_workers", 6)))

        level = max(1, min(5, int(s.get("confidence", 3))))
        self._current_level = level
        self._slider.set(level)
        self._lbl_desc.configure(text=_LEVEL_LABELS[level])
        self._apply_preset(level)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _build_ui(self) -> None:
        # Row 6 (log) gets all spare vertical space
        self.rowconfigure(6, weight=1)
        self.columnconfigure(0, weight=1)

        PAD = {"padx": 15, "pady": (8, 0)}

        # ── Row 0: Paths ─────────────────────────────────────────────────
        paths = ctk.CTkFrame(self)
        paths.grid(row=0, column=0, sticky="ew", **PAD)
        paths.columnconfigure(1, weight=1)

        ctk.CTkLabel(paths, text="Paths",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, columnspan=3, sticky="w", padx=12, pady=(8, 4)
        )

        ctk.CTkLabel(paths, text="Input dir:", anchor="e", width=80).grid(
            row=1, column=0, sticky="e", padx=(12, 6), pady=4
        )
        self._ent_input = ctk.CTkEntry(paths, placeholder_text="Source texture folder…")
        self._ent_input.grid(row=1, column=1, sticky="ew", padx=(0, 6), pady=4)
        ctk.CTkButton(paths, text="Browse", width=80,
                      command=self._browse_input).grid(
            row=1, column=2, padx=(0, 12), pady=4
        )

        ctk.CTkLabel(paths, text="Output dir:", anchor="e", width=80).grid(
            row=2, column=0, sticky="e", padx=(12, 6), pady=(0, 8)
        )
        self._ent_output = ctk.CTkEntry(paths, placeholder_text="Destination / library folder…")
        self._ent_output.grid(row=2, column=1, sticky="ew", padx=(0, 6), pady=(0, 8))
        ctk.CTkButton(paths, text="Browse", width=80,
                      command=self._browse_output).grid(
            row=2, column=2, padx=(0, 12), pady=(0, 8)
        )

        # ── Row 1: Confidence slider ──────────────────────────────────────
        conf = ctk.CTkFrame(self)
        conf.grid(row=1, column=0, sticky="ew", **PAD)
        conf.columnconfigure(1, weight=1)

        ctk.CTkLabel(conf, text="Input Confidence",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(8, 2)
        )

        ctk.CTkLabel(conf, text="1", text_color="#888888").grid(
            row=1, column=0, padx=(12, 4), pady=4
        )
        self._slider = ctk.CTkSlider(
            conf, from_=1, to=5, number_of_steps=4,
            command=self._on_slider_drag,
        )
        self._slider.grid(row=1, column=1, sticky="ew", padx=4, pady=4)
        ctk.CTkLabel(conf, text="5", text_color="#888888").grid(
            row=1, column=2, padx=(4, 12), pady=4
        )

        self._lbl_desc = ctk.CTkLabel(
            conf, text=_LEVEL_LABELS[3],
            text_color="#8888aa", wraplength=750, justify="left",
            font=ctk.CTkFont(size=11),
        )
        self._lbl_desc.grid(row=2, column=0, columnspan=4,
                            sticky="w", padx=12, pady=(0, 8))

        # ── Row 2: Quick settings ─────────────────────────────────────────
        quick = ctk.CTkFrame(self)
        quick.grid(row=2, column=0, sticky="ew", **PAD)
        quick.columnconfigure(1, weight=1)

        ctk.CTkLabel(quick, text="Quick Settings",
                     font=ctk.CTkFont(size=13, weight="bold")).grid(
            row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(8, 4)
        )

        ctk.CTkLabel(quick, text="AI model:", anchor="e", width=80).grid(
            row=1, column=0, sticky="e", padx=(12, 6), pady=(0, 8)
        )
        self._ent_model = ctk.CTkEntry(quick, placeholder_text="gemma4:e4b")
        self._ent_model.grid(row=1, column=1, sticky="ew", padx=(0, 24), pady=(0, 8))

        ctk.CTkLabel(quick, text="CPU workers:", anchor="e").grid(
            row=1, column=2, sticky="e", padx=(0, 6), pady=(0, 8)
        )
        self._ent_workers = ctk.CTkEntry(quick, width=60)
        self._ent_workers.grid(row=1, column=3, sticky="w", padx=(0, 12), pady=(0, 8))

        # ── Row 3: Advanced toggle button ─────────────────────────────────
        self._btn_adv = ctk.CTkButton(
            self, text="▶  Advanced Settings",
            fg_color="transparent", hover_color="#2a2d2e",
            text_color="#7777aa", anchor="w",
            font=ctk.CTkFont(size=12),
            command=self._toggle_advanced,
        )
        self._btn_adv.grid(row=3, column=0, sticky="w", padx=15, pady=(8, 0))

        # ── Row 4: Advanced frame (hidden until toggled) ──────────────────
        self._adv_frame = ctk.CTkFrame(self)
        self._build_advanced(self._adv_frame)
        # Not gridded here — _toggle_advanced manages visibility.

        # ── Row 5: Button row ─────────────────────────────────────────────
        btn_outer = ctk.CTkFrame(self, fg_color="transparent")
        btn_outer.grid(row=5, column=0, sticky="ew", padx=15, pady=(10, 4))

        self._btn_run = ctk.CTkButton(
            btn_outer, text="▶  Run Pipeline", width=160,
            fg_color="#2d6a4f", hover_color="#1b4332",
            command=self._run_pipeline,
        )
        self._btn_run.pack(side="left", padx=(0, 8))

        self._btn_stop = ctk.CTkButton(
            btn_outer, text="■  Stop", width=90,
            fg_color="#6d2e2e", hover_color="#4a1f1f",
            state="disabled",
            command=self._stop_pipeline,
        )
        self._btn_stop.pack(side="left", padx=(0, 20))

        self._btn_preview = ctk.CTkButton(
            btn_outer, text="Generate Preview", width=150,
            fg_color="#1e3a5f", hover_color="#162a44",
            command=self._generate_preview,
        )
        self._btn_preview.pack(side="left", padx=(0, 8))

        self._btn_rescan = ctk.CTkButton(
            btn_outer, text="Rescan Library", width=140,
            fg_color="#3a2060", hover_color="#281545",
            command=self._rescan_library,
        )
        self._btn_rescan.pack(side="left")

        self._lbl_status = ctk.CTkLabel(
            btn_outer, text="", text_color="#888888",
            font=ctk.CTkFont(size=11),
        )
        self._lbl_status.pack(side="left", padx=14)

        # ── Row 6: Log ────────────────────────────────────────────────────
        log_outer = ctk.CTkFrame(self)
        log_outer.grid(row=6, column=0, sticky="nsew", padx=15, pady=(4, 12))
        log_outer.rowconfigure(1, weight=1)
        log_outer.columnconfigure(0, weight=1)

        ctk.CTkLabel(log_outer, text="Log",
                     font=ctk.CTkFont(size=11, weight="bold"),
                     text_color="#666688").grid(
            row=0, column=0, sticky="w", padx=10, pady=(6, 2)
        )

        self._log = ctk.CTkTextbox(
            log_outer,
            state="disabled",
            font=ctk.CTkFont(family="Consolas", size=11),
            wrap="word",
        )
        self._log.grid(row=1, column=0, sticky="nsew", padx=8, pady=(0, 8))

        # Colour tags on the underlying tk.Text widget
        tb = self._log._textbox
        tb.tag_config("error",   foreground="#ff6b6b")
        tb.tag_config("warning", foreground="#ffd93d")
        tb.tag_config("success", foreground="#6bcb77")
        tb.tag_config("info",    foreground="#74b9e7")

    def _build_advanced(self, parent: ctk.CTkFrame) -> None:
        """Populate the (collapsible) advanced settings frame."""
        parent.columnconfigure(1, weight=1)
        parent.columnconfigure(3, weight=1)

        # Section header
        ctk.CTkLabel(
            parent, text="Filter Thresholds",
            font=ctk.CTkFont(size=12, weight="bold"),
        ).grid(row=0, column=0, columnspan=4, sticky="w", padx=12, pady=(10, 4))

        # Helper: place a label + entry pair
        def _field(row: int, col_offset: int,
                   label: str, attr: str, width: int = 90) -> None:
            lbl_col = col_offset * 2
            ent_col = col_offset * 2 + 1
            ctk.CTkLabel(parent, text=label, anchor="e").grid(
                row=row, column=lbl_col,
                sticky="e", padx=(12 if col_offset == 0 else 0, 4), pady=3,
            )
            ent = ctk.CTkEntry(parent, width=width)
            ent.grid(row=row, column=ent_col,
                     sticky="w", padx=(0, 20 if col_offset == 0 else 12), pady=3)
            setattr(self, attr, ent)

        _field(1, 0, "Blank StdDev:",      "_ent_blank")
        _field(1, 1, "Min Resolution (px):", "_ent_minres", width=80)

        _field(2, 0, "Product Edge:",       "_ent_edge")
        _field(2, 1, "pHash Hamming dist.:", "_ent_hamming", width=80)

        _field(3, 0, "Line Art ratio:",     "_ent_lineart")

        _field(4, 0, "Tile Gradient:",      "_ent_gradient")
        _field(4, 1, "Tile Seam Diff:",     "_ent_seam",    width=80)

        _field(5, 0, "Tile Offset Seam:",   "_ent_offset_seam")

        # Checkboxes
        chk_row = ctk.CTkFrame(parent, fg_color="transparent")
        chk_row.grid(row=6, column=0, columnspan=4, sticky="w", padx=10, pady=(8, 2))

        self._var_autobin = ctk.IntVar(value=0)
        self._chk_autobin = ctk.CTkCheckBox(
            chk_row, text="Auto-bin tileability failures",
            variable=self._var_autobin,
        )
        self._chk_autobin.pack(side="left", padx=(2, 30))

        self._var_skip = ctk.IntVar(value=0)
        self._chk_skip = ctk.CTkCheckBox(
            chk_row, text="Skip quality checks  (Level 5 only)",
            variable=self._var_skip,
            command=self._on_skip_toggle,
        )
        self._chk_skip.pack(side="left")

        ctk.CTkLabel(
            parent,
            text=(
                "Skip quality checks bypasses the blank / line-art / product-photo "
                "pre-filters and the tileability test. Only enable for verified "
                "seamless professional sources."
            ),
            text_color="#555570",
            wraplength=700,
            justify="left",
            font=ctk.CTkFont(size=10),
        ).grid(row=7, column=0, columnspan=4, sticky="w", padx=12, pady=(0, 10))

    # ------------------------------------------------------------------
    # Slider / preset interaction
    # ------------------------------------------------------------------

    def _on_slider_drag(self, value: float) -> None:
        new_level = round(value)
        if new_level == self._current_level:
            return
        # Persist current preset before switching
        self._capture_preset()
        self._current_level = new_level
        self._lbl_desc.configure(text=_LEVEL_LABELS[new_level])
        self._apply_preset(new_level)

    def _update_widget_states(self, level: int) -> None:
        """Enable/disable widgets that depend on the current confidence level."""
        # Skip-checks checkbox: only interactive at level 5
        if level == 5:
            self._chk_skip.configure(state="normal")
        else:
            self._var_skip.set(0)
            self._chk_skip.configure(state="disabled")

        self._sync_tile_field_states()

    def _on_skip_toggle(self) -> None:
        self._sync_tile_field_states()

    def _sync_tile_field_states(self) -> None:
        """Disable tile-threshold entries when skip_checks is active."""
        state = "disabled" if self._var_skip.get() else "normal"
        self._ent_gradient.configure(state=state)
        self._ent_seam.configure(state=state)
        self._ent_offset_seam.configure(state=state)

    # ------------------------------------------------------------------
    # Browse callbacks
    # ------------------------------------------------------------------

    def _browse_input(self) -> None:
        path = filedialog.askdirectory(title="Select Input Texture Folder")
        if path:
            self._ent_input.delete(0, "end")
            self._ent_input.insert(0, path)

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Select Output / Library Folder")
        if path:
            self._ent_output.delete(0, "end")
            self._ent_output.insert(0, path)

    # ------------------------------------------------------------------
    # Advanced panel toggle
    # ------------------------------------------------------------------

    def _toggle_advanced(self) -> None:
        self._adv_visible = not self._adv_visible
        if self._adv_visible:
            self._adv_frame.grid(row=4, column=0, sticky="ew", padx=15, pady=(2, 0))
            self._btn_adv.configure(text="▼  Advanced Settings")
        else:
            self._adv_frame.grid_remove()
            self._btn_adv.configure(text="▶  Advanced Settings")

    # ------------------------------------------------------------------
    # Pipeline execution
    # ------------------------------------------------------------------

    def _run_pipeline(self) -> None:
        input_dir  = self._ent_input.get().strip()
        output_dir = self._ent_output.get().strip()

        if not input_dir or not output_dir:
            self._set_status("Input and output directories are required.", error=True)
            return
        if not Path(input_dir).is_dir():
            self._set_status("Input directory does not exist.", error=True)
            return

        self._save_settings()
        p = self._settings["presets"][str(self._current_level)]
        s = self._settings["last_session"]

        cmd = [
            sys.executable, str(_MAIN_PY),
            "--input",               input_dir,
            "--output",              output_dir,
            "--ai-model",            s.get("ai_model", "gemma4:e4b"),
            "--cpu-workers",         str(s.get("cpu_workers", 6)),
            "--blank-stddev",        str(p["blank_stddev"]),
            "--product-edge-stddev", str(p["product_edge"]),
            "--line-art-threshold",  str(p["line_art"]),
            "--tile-gradient",       str(p["tile_gradient"]),
            "--tile-seam-diff",      str(p["tile_seam"]),
            "--tile-offset-seam",    str(p["tile_offset_seam"]),
            "--phash-hamming",       str(p["phash_hamming"]),
            "--min-resolution",      str(p["min_resolution"]),
        ]
        if p["auto_bin"]:
            cmd.append("--auto-bin-tileability")
        if p["skip_checks"]:
            cmd.append("--skip-quality-checks")

        self._start_subprocess(cmd, cwd=str(_PIPELINE_DIR), status="Running pipeline…")

    def _stop_pipeline(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
            self._append_log("\n[Process terminated by user]\n", tag="warning")
            self._set_status("Stopped.")
            self._reset_buttons()

    def _generate_preview(self) -> None:
        output_dir = self._ent_output.get().strip()
        if not output_dir:
            self._set_status("Output directory required for preview.", error=True)
            return

        self._save_settings()
        cmd = [sys.executable, str(_PREVIEW_PY), "--output", output_dir]
        self._start_subprocess(cmd, cwd=str(_HERE), status="Generating preview…")

    def _rescan_library(self) -> None:
        """
        Re-run all algorithmic filters (no AI) on the organised library.
        Textures that no longer pass the current thresholds are moved to
        _needs_review/almost_passed/<Category>/<GroupName>/.
        """
        output_dir = self._ent_output.get().strip()
        if not output_dir:
            self._set_status("Output directory required for rescan.", error=True)
            return

        self._save_settings()
        p = self._settings["presets"][str(self._current_level)]

        cmd = [
            sys.executable, str(_RESCAN_PY),
            "--library",             output_dir,
            "--blank-stddev",        str(p["blank_stddev"]),
            "--product-edge-stddev", str(p["product_edge"]),
            "--line-art-threshold",  str(p["line_art"]),
            "--tile-gradient",       str(p["tile_gradient"]),
            "--tile-seam-diff",      str(p["tile_seam"]),
            "--tile-offset-seam",    str(p["tile_offset_seam"]),
            "--min-resolution",      str(p["min_resolution"]),
        ]
        self._start_subprocess(
            cmd, cwd=str(_PIPELINE_DIR), status="Rescanning library…"
        )

    def _start_subprocess(
        self, cmd: list[str], cwd: str, status: str
    ) -> None:
        """Launch *cmd* as a subprocess and begin streaming its output to the log."""
        self._clear_log()
        self._append_log("$ " + " ".join(f'"{a}"' if " " in a else a for a in cmd) + "\n", tag="info")
        self._append_log("─" * 60 + "\n", tag="info")

        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
                encoding="utf-8",
                errors="replace",
            )
        except OSError as exc:
            self._set_status(f"Failed to launch: {exc}", error=True)
            return

        self._btn_run.configure(state="disabled")
        self._btn_stop.configure(state="normal")
        self._btn_preview.configure(state="disabled")
        self._btn_rescan.configure(state="disabled")
        self._set_status(status)

        # Drain stdout in a daemon thread → queue → GUI thread
        threading.Thread(
            target=self._stream_logs,
            args=(self._proc,),
            daemon=True,
        ).start()
        self.after(100, self._poll_log_queue)

    # ------------------------------------------------------------------
    # Log streaming
    # ------------------------------------------------------------------

    def _stream_logs(self, proc: subprocess.Popen) -> None:
        """Background thread: read subprocess output line-by-line into the queue."""
        assert proc.stdout is not None
        for line in proc.stdout:
            self._log_queue.put(line)
        proc.wait()
        self._log_queue.put(None)   # sentinel: process has exited

    def _poll_log_queue(self) -> None:
        """GUI timer: drain the log queue and append lines to the textbox."""
        try:
            while True:
                item = self._log_queue.get_nowait()
                if item is None:
                    self._on_subprocess_done()
                    return
                self._append_log(item)
        except queue.Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _on_subprocess_done(self) -> None:
        rc = self._proc.returncode if self._proc else 0
        if rc == 0:
            self._append_log("\n✓ Process completed successfully.\n", tag="success")
            self._set_status("Completed.")
        else:
            self._append_log(f"\n✗ Process exited with code {rc}.\n", tag="error")
            self._set_status(f"Exited with code {rc}.", error=True)
        self._proc = None
        self._reset_buttons()

    # ------------------------------------------------------------------
    # Log helpers
    # ------------------------------------------------------------------

    def _append_log(self, line: str, tag: str | None = None) -> None:
        """Append *line* to the log textbox, applying colour tags automatically."""
        if tag is None:
            u = line.upper()
            if any(k in u for k in ("ERROR", "CRITICAL", "TRACEBACK", "EXCEPTION")):
                tag = "error"
            elif "WARNING" in u:
                tag = "warning"
            elif any(k in u for k in ("COMPLETED", "✓", "SUCCESS")):
                tag = "success"

        tb = self._log._textbox
        tb.configure(state="normal")
        if tag:
            tb.insert("end", line, tag)
        else:
            tb.insert("end", line)
        tb.see("end")
        tb.configure(state="disabled")

    def _clear_log(self) -> None:
        self._log.configure(state="normal")
        self._log.delete("0.0", "end")
        self._log.configure(state="disabled")

    # ------------------------------------------------------------------
    # Status / button helpers
    # ------------------------------------------------------------------

    def _set_status(self, msg: str, error: bool = False) -> None:
        self._lbl_status.configure(
            text=msg, text_color="#ff6b6b" if error else "#888888"
        )

    def _reset_buttons(self) -> None:
        self._btn_run.configure(state="normal")
        self._btn_stop.configure(state="disabled")
        self._btn_preview.configure(state="normal")
        self._btn_rescan.configure(state="normal")

    # ------------------------------------------------------------------
    # Window close
    # ------------------------------------------------------------------

    def _on_close(self) -> None:
        if self._proc and self._proc.poll() is None:
            self._proc.terminate()
        self._save_settings()
        self.destroy()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    app = PipelineGUI()
    app.mainloop()
