#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TikTok HQ - modern desktop window.

A native OS web view (pywebview: WebView2 on Windows, WKWebView on macOS)
showing ui/index.html. The page talks to the `Api` class below through
pywebview's js_api bridge; the engine (tiktok_hq.py) is unchanged and runs in a
worker thread, its output lines are queued and the page polls them.

`Api` has no dependency on pywebview, so it is unit-tested without a display.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import tiktok_hq as core

APP_TITLE = "TikTok HQ"
VIDEO_FILTER = "Video (*.mp4;*.mov;*.m4v;*.mkv;*.webm;*.avi;*.mts;*.ts;*.MP4;*.MOV)"
ALL_FILTER = "All files (*.*)"
TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
DONE_RE = re.compile(r"^DONE -> (.+?)\s+\([^()]*\)\s*$")
VERDICTS = ("IDENTICAL", "SAME MEDIA", "SAME PICTURE", "RE-ENCODED")


def ui_dir() -> Path:
    """The folder holding index.html: next to this file, or inside the packaged app."""
    for d in core._app_dirs():
        if (d / "ui" / "index.html").is_file():
            return d / "ui"
    return Path(__file__).resolve().parent / "ui"


def prefs_path() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "TikTokHQ" / "prefs.json"


def open_folder(path: Path) -> None:
    try:
        if os.name == "nt":
            os.startfile(str(path))  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception:
        pass


class Api:
    """Methods the page calls (pywebview js_api). Every public method must return JSON-serializable data."""

    def __init__(self) -> None:
        self._events: "queue.Queue[dict]" = queue.Queue()
        self._lock = threading.Lock()
        self._busy = False
        self._window: Any = None
        self._duration = 0.0
        self._what = ""
        self._outputs: List[str] = []
        self._verdict: Optional[str] = None
        self.renderer: Optional[str] = None

    # ----------------------------------------------------------------- wiring
    def attach(self, window: Any) -> None:
        self._window = window

    def _emit(self, kind: str, **payload: Any) -> None:
        payload["kind"] = kind
        self._events.put(payload)

    def poll(self) -> List[dict]:
        """Drain queued events (the page calls this on a timer)."""
        out: List[dict] = []
        try:
            while len(out) < 500:
                out.append(self._events.get_nowait())
        except queue.Empty:
            pass
        return out

    # ------------------------------------------------------------------- info
    def info(self) -> dict:
        d: Dict[str, Any] = {
            "version": core.VERSION,
            "frozen": core.is_frozen(),
            "platform": sys.platform,
            "renderer": self.renderer,
            "busy": self._busy,
        }
        try:
            ffmpeg, ffprobe = core.require_tools()
            d.update(tools_ok=True, ffmpeg=ffmpeg, ffprobe=ffprobe)
        except core.ToolError as e:
            d.update(tools_ok=False, tools_error=str(e))
        return d

    def get_prefs(self) -> dict:
        try:
            return json.loads(prefs_path().read_text(encoding="utf-8"))
        except Exception:
            return {}

    def set_prefs(self, prefs: dict) -> bool:
        try:
            p = prefs_path()
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(prefs, ensure_ascii=False, indent=1), encoding="utf-8")
            return True
        except Exception:
            return False

    # ---------------------------------------------------------------- dialogs
    def pick_file(self) -> Optional[str]:
        if self._window is None:
            return None
        import webview  # noqa: WPS433  (only when a window exists)
        try:
            r = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                                                file_types=(VIDEO_FILTER, ALL_FILTER))
        except Exception:
            return None
        if not r:
            return None
        return str(r[0]) if isinstance(r, (list, tuple)) else str(r)

    def pick_dir(self) -> Optional[str]:
        if self._window is None:
            return None
        import webview
        try:
            r = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        except Exception:
            return None
        if not r:
            return None
        return str(r[0]) if isinstance(r, (list, tuple)) else str(r)

    def open_folder(self, path: str) -> bool:
        p = Path(path).expanduser()
        target = p if p.is_dir() else p.parent
        if not target.is_dir():
            return False
        open_folder(target)
        return True

    # ------------------------------------------------------------------ probe
    def probe(self, path: str) -> dict:
        """Analyse a file for the file card: what it is and what the engine would do with it."""
        src = Path(path).expanduser()
        if not src.is_file():
            return {"ok": False, "error": f"file not found: {src}"}
        try:
            _, ffprobe = core.require_tools()
            an = core.probe(ffprobe, src)
        except core.ToolError as e:
            return {"ok": False, "error": str(e)}
        w, h = an.display_size
        d: Dict[str, Any] = {
            "ok": True, "path": str(src), "name": src.name, "size": an.size, "size_h": core.human_size(an.size),
            "duration": an.duration, "width": w, "height": h, "fps": an.fps, "vcodec": an.vcodec,
            "vprofile": an.vprofile, "pix_fmt": an.pix_fmt, "hdr": an.is_hdr, "acodec": an.acodec,
            "sample_rate": an.sample_rate, "channels": an.channels, "vbitrate": an.vbitrate,
            "already_output": core.OUTPUT_SUFFIX in src.stem, "summary": an.summary_lines(),
        }
        try:
            dec = core.decide(an)
            d.update(plan_video=dec.video, plan_audio=dec.audio, reasons=dec.reasons, warnings=dec.warnings)
        except core.ToolError as e:
            d.update(plan_video="error", plan_audio="error", reasons=[str(e)], warnings=[])
        self._duration = an.duration
        return d

    # ------------------------------------------------------------------- jobs
    def prepare(self, path: str, options: Optional[dict] = None) -> dict:
        o = options or {}
        argv = ["prep", str(path), "--method", str(o.get("method") or "audio"), "--tags", str(o.get("tags") or "replica")]
        if o.get("force_encode"):
            argv.append("--force-encode")
        if o.get("out_dir"):
            argv += ["--out-dir", str(o["out_dir"])]
        if o.get("preset"):
            argv += ["--preset", str(o["preset"])]
        if o.get("multiplier"):
            argv += ["--multiplier", str(int(o["multiplier"]))]
        return self._start(argv, "prep")

    def check(self, path: str) -> dict:
        return self._start(["check", str(path)], "check")

    def compare(self, served: str, uploaded: str) -> dict:
        return self._start(["compare", str(served), str(uploaded)], "compare")

    def _start(self, argv: List[str], what: str) -> dict:
        with self._lock:
            if self._busy:
                return {"started": False, "error": "busy"}
            self._busy = True
        self._what = what
        t = threading.Thread(target=self._run, args=(argv, what), daemon=True)
        t.start()
        return {"started": True}

    def _run(self, argv: List[str], what: str) -> None:
        core.set_output(self._line, self._progress)
        rc = 1
        outputs: List[str] = []
        verdict = None
        self._emit("start", what=what)
        try:
            ns = core.build_parser().parse_args(argv)
            if what == "prep" and not self._duration:
                try:
                    _, ffprobe = core.require_tools()
                    self._duration = core.probe(ffprobe, Path(ns.input).expanduser()).duration
                except Exception:
                    self._duration = 0.0
            if ns.cmd == "check":
                rc = core.cmd_check(ns)
            elif ns.cmd == "compare":
                rc = core.cmd_compare(ns)
            else:
                rc = core.cmd_prep(ns)
        except core.ToolError as e:
            self._emit("line", text=f"ERROR: {e}")
            self._emit("error", message=str(e))
            rc = 1
        except SystemExit as e:  # argparse
            self._emit("error", message=f"bad arguments ({e})")
            rc = 2
        except Exception:  # noqa: BLE001
            tb = traceback.format_exc()
            self._emit("line", text="UNEXPECTED ERROR:\n" + tb)
            self._emit("error", message=tb.strip().splitlines()[-1])
            rc = 1
        finally:
            core.set_output(core._print_line, core._print_progress)
            outputs = list(self._outputs)
            verdict = self._verdict
            self._outputs = []
            self._verdict = None
            with self._lock:
                self._busy = False
            self._emit("done", what=what, rc=rc, outputs=outputs,
                       out_dir=str(Path(outputs[0]).parent) if outputs else None, verdict=verdict)

    def _line(self, text: str) -> None:
        m = DONE_RE.match(text)
        if m:
            self._outputs.append(m.group(1).strip())
        for v in VERDICTS:
            if text.startswith(v):
                self._verdict = v
        if text.startswith("[1/3]"):
            self._emit("stage", stage=1)
        elif text.startswith("[2/3]"):
            self._emit("stage", stage=2)
        elif text.startswith("[3/3]"):
            self._emit("stage", stage=3)
        self._emit("line", text=text)

    def _progress(self, text: str) -> None:
        if not text:
            self._emit("progress", pct=None, text="")
            return
        pct = None
        m = TIME_RE.search(text)
        if m and self._duration:
            t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            pct = max(0.0, min(99.0, t / self._duration * 100.0))
        self._emit("progress", pct=pct, text=text)

    # -------------------------------------------------------------- drag-drop
    def _on_drop(self, event: dict) -> None:
        files = (event or {}).get("dataTransfer", {}).get("files", []) or []
        for f in files:
            p = f.get("pywebviewFullPath")
            if p:
                self._emit("file", path=p)
                return
        self._emit("file_nopath")


# --------------------------------------------------------------------------- #
# window
# --------------------------------------------------------------------------- #

def run(initial_file: Optional[str] = None) -> int:
    import webview
    from webview.dom import DOMEventHandler

    html = ui_dir() / "index.html"
    if not html.is_file():
        raise RuntimeError(f"ui/index.html not found ({html})")

    api = Api()
    window = webview.create_window(
        APP_TITLE, url=str(html), js_api=api,
        width=1040, height=760, min_size=(860, 620),
        background_color="#0f1115", text_select=False,
    )
    api.attach(window)
    state = {"fallback": False}

    def on_initialized(renderer=None):
        api.renderer = renderer

    def on_loaded():
        if api.renderer == "mshtml" and not state["fallback"]:
            # old Windows without the WebView2 runtime: Internet Explorer engine cannot show the app
            state["fallback"] = True
            window.load_url(str(ui_dir() / "fallback.html"))
            return
        try:
            el = window.dom.get_element("#drop")
            if el is not None:
                el.on("drop", DOMEventHandler(api._on_drop, prevent_default=True))
        except Exception:
            pass
        if initial_file:
            api._emit("file", path=str(Path(initial_file).expanduser()))

    window.events.initialized += on_initialized
    window.events.loaded += on_loaded
    webview.start(debug=bool(os.environ.get("TIKTOKHQ_DEBUG")), private_mode=True)
    return 0
