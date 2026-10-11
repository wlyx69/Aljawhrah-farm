#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TikTok HQ - modern desktop window.

A native OS web view (pywebview: WebView2 on Windows, WKWebView on macOS)
showing ui/index.html. The page talks to the `Api` class below through
pywebview's js_api bridge; the engine (tiktok_hq.py) is unchanged and runs in a
worker thread, its output lines are queued and the page polls them.

`Api` has no dependency on pywebview, so it is unit-tested without a display.
`run()` raises ModernUnavailable when the web view cannot be used on this
machine (no WebView2 runtime, or the page never loads); the launcher
(tiktok_hq_gui.py) then opens the classic window instead.
"""
from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import sys
import threading
import time
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
LOAD_TIMEOUT = 45.0  # seconds the window may stay blank before giving up (Windows)
WEBVIEW2_URL = "https://developer.microsoft.com/microsoft-edge/webview2/"

# Engine messages -> codes the page shows in Arabic/English (unmatched text is shown as is).
REASON_CODES = [
    ("codec", re.compile(r"^video codec is (\S+);")),
    ("pixfmt", re.compile(r"^pixel format is (\S+) ")),
    ("hdr", re.compile(r"^source is HDR")),
    ("res_high", re.compile(r"^resolution (\d+)x(\d+) is above 1080p")),
    ("fps_high", re.compile(r"^frame rate ([\d.]+) is above 60")),
    ("interlaced", re.compile(r"^video is interlaced")),
    ("profile", re.compile(r"^H\.264 profile (.+?) is unusual")),
    ("audio", re.compile(r"^audio \S+ \d+ch \d+Hz will be re-encoded")),
    ("forced", re.compile(r"^--force-encode requested")),
    ("no_video", re.compile(r"^the file has no video stream")),
]
WARNING_CODES = [
    ("no_audio", re.compile(r"^no audio track")),
    ("over_4gb", re.compile(r"above TikTok's 4 GB upload limit")),
    ("fps_low", re.compile(r"^frame rate ([\d.]+) is below")),
    ("res_low", re.compile(r"^resolution (\d+)x(\d+) is below")),
    ("letterbox", re.compile(r"^(\d+)x(\d+) is not 1080x1920")),
    ("level", re.compile(r"^H\.264 level ([\d.]+) is above")),
    ("vfr", re.compile(r"^variable frame rate \(nominal ([\d.]+), average ([\d.]+)\)")),
]


def coded(messages: Optional[List[str]], table) -> List[dict]:
    """Tag each engine message with a code + arguments so the page can translate it."""
    out: List[dict] = []
    for text in messages or []:
        item: Dict[str, Any] = {"code": None, "args": [], "text": text}
        for code, rx in table:
            m = rx.search(text)
            if m:
                item["code"], item["args"] = code, list(m.groups())
                break
        out.append(item)
    return out


class ModernUnavailable(RuntimeError):
    """The web-view window cannot be used here. reason: 'webview2' (runtime missing) or 'noload'."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


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
        self._job_duration = 0.0  # duration of the file the running job works on (progress %)
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
        """Drain queued events (the page calls this on a timer, one request at a time)."""
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
            data = json.loads(prefs_path().read_text(encoding="utf-8"))
            return data if isinstance(data, dict) else {}
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
    def _dialog(self, kind: str) -> Optional[str]:
        if self._window is None:
            return None
        import webview  # only when a window exists
        try:
            if kind == "file":
                r = self._window.create_file_dialog(webview.FileDialog.OPEN, allow_multiple=False,
                                                    file_types=(VIDEO_FILTER, ALL_FILTER))
            else:
                r = self._window.create_file_dialog(webview.FileDialog.FOLDER)
        except Exception:
            return None
        if not r:
            return None
        return str(r[0]) if isinstance(r, (list, tuple)) else str(r)

    def pick_file(self) -> Optional[str]:
        return self._dialog("file")

    def pick_dir(self) -> Optional[str]:
        return self._dialog("dir")

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
        try:
            src = Path(path).expanduser()
            if not src.is_file():
                return {"ok": False, "error": f"file not found: {src}"}
            _, ffprobe = core.require_tools()
            an = core.probe(ffprobe, src)
            w, h = an.display_size
            d: Dict[str, Any] = {
                "ok": True, "path": str(src), "name": src.name, "size": an.size,
                "size_h": core.human_size(an.size), "duration": an.duration, "width": w, "height": h,
                "fps": an.fps, "vcodec": an.vcodec, "vprofile": an.vprofile, "pix_fmt": an.pix_fmt,
                "hdr": an.is_hdr, "acodec": an.acodec, "sample_rate": an.sample_rate,
                "channels": an.channels, "vbitrate": an.vbitrate,
                "already_output": core.OUTPUT_SUFFIX in src.stem, "summary": an.summary_lines(),
            }
            try:
                dec = core.decide(an)
                d.update(plan_video=dec.video, plan_audio=dec.audio, reasons=dec.reasons, warnings=dec.warnings)
            except core.ToolError as e:
                d.update(plan_video="error", plan_audio="error", reasons=[str(e)], warnings=[])
            d["reasons_coded"] = coded(d["reasons"], REASON_CODES)
            d["warnings_coded"] = coded(d["warnings"], WARNING_CODES)
            return d
        except core.ToolError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001  (timeouts, unreadable files, bad ffprobe output)
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # ------------------------------------------------------------------- jobs
    def prepare(self, path: str, options: Optional[dict] = None) -> dict:
        try:
            o = options if isinstance(options, dict) else {}
            argv = ["prep", str(path), "--method", str(o.get("method") or "audio"),
                    "--tags", str(o.get("tags") or "replica")]
            if o.get("force_encode"):
                argv.append("--force-encode")
            if o.get("out_dir"):
                argv += ["--out-dir", str(o["out_dir"])]
            if o.get("preset"):
                argv += ["--preset", str(o["preset"])]
            if o.get("multiplier"):
                argv += ["--multiplier", str(int(o["multiplier"]))]
        except Exception as e:  # noqa: BLE001
            return {"started": False, "error": f"bad options: {e}"}
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
        try:
            threading.Thread(target=self._run, args=(argv, what), daemon=True).start()
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._busy = False
            return {"started": False, "error": str(e)}
        return {"started": True}

    def _run(self, argv: List[str], what: str) -> None:
        core.set_output(self._line, self._progress)
        rc = 1
        failed = False
        self._job_duration = 0.0
        self._emit("start", what=what)
        try:
            ns = core.build_parser().parse_args(argv)
            if what == "prep":
                try:
                    _, ffprobe = core.require_tools()
                    self._job_duration = core.probe(ffprobe, Path(ns.input).expanduser()).duration
                except Exception:
                    self._job_duration = 0.0
            if ns.cmd == "check":
                rc = core.cmd_check(ns)
            elif ns.cmd == "compare":
                rc = core.cmd_compare(ns)
            else:
                rc = core.cmd_prep(ns)
        except core.ToolError as e:
            failed, rc = True, 1
            self._emit("line", text=f"ERROR: {e}")
            self._emit("error", message=str(e))
        except SystemExit as e:  # argparse
            failed, rc = True, 2
            self._emit("error", message=f"bad arguments ({e})")
        except Exception:  # noqa: BLE001
            failed, rc = True, 1
            tb = traceback.format_exc()
            self._emit("line", text="UNEXPECTED ERROR:\n" + tb)
            self._emit("error", message=tb.strip().splitlines()[-1])
        finally:
            core.set_output(core._print_line, core._print_progress)
            outputs = list(self._outputs)
            verdict = self._verdict
            self._outputs = []
            self._verdict = None
            with self._lock:
                self._busy = False
            self._emit("done", what=what, rc=rc, error=failed, outputs=outputs,
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
        if m and self._job_duration:
            t = int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
            pct = max(0.0, min(99.0, t / self._job_duration * 100.0))
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

def windows_renderer() -> str:
    """Load pywebview's WinForms backend (pythonnet + bundled WebView2 interop DLLs) and return its renderer.

    'edgechromium' when the WebView2 runtime is installed, 'mshtml' (Internet Explorer) when it is not.
    Raises if pythonnet or the bundled DLLs are broken.
    """
    import webview.platforms.winforms as wf
    return str(getattr(wf, "renderer", ""))


def _js_wait(window: Any, expr: str, timeout: float) -> Any:
    """Evaluate `expr` until it returns something truthy (or the timeout passes); return the last value."""
    deadline = time.monotonic() + timeout
    value: Any = None
    while time.monotonic() < deadline:
        try:
            value = window.evaluate_js(expr)
        except Exception:  # noqa: BLE001  (page busy or navigating: try again)
            value = None
        if value:
            return value
        time.sleep(0.25)
    return value


FILECARD_JS = ("document.getElementById('filecard').classList.contains('hidden') ? '' : "
               "document.getElementById('f-name').textContent")
RESULT_JS = ("(function(){var r=document.getElementById('result'), e=document.getElementById('error');"
             "if(!r.classList.contains('hidden')) return 'OK ' + document.getElementById('outputs').textContent;"
             "if(!e.classList.contains('hidden')) return 'ERROR ' + document.getElementById('error-text').textContent;"
             "return '';})()")


def _selftest(window: Any, api: Api, out_path: str, initial_file: Optional[str], state: dict) -> None:
    """CI: drive the real window (bridge, RTL page, file card, Prepare) and write the results to out_path."""
    lines: List[str] = []
    ok = True
    try:
        if not window.events.loaded.wait(60):
            raise RuntimeError("the page did not load within 60 s")
        lines.append(f"renderer={api.renderer}")
        expected = json.dumps("v" + core.VERSION)
        booted = _js_wait(window, f"document.getElementById('version').textContent === {expected} ? 'yes' : ''", 30)
        lines.append(f"bridge={'ok' if booted == 'yes' else 'FAIL'}")
        ok = ok and booted == "yes"
        direction = window.evaluate_js("document.documentElement.dir + '/' + document.documentElement.lang")
        lines.append(f"direction={direction}")
        ok = ok and direction == "rtl/ar"
        if initial_file:
            name = _js_wait(window, FILECARD_JS, 60)
            lines.append(f"file_card={name}")
            ok = ok and name == Path(initial_file).name
            window.evaluate_js("document.getElementById('btn-prepare').click()")
            res = str(_js_wait(window, RESULT_JS, 300) or "")
            lines.append("prepare=" + res.replace("\n", " | "))
            out = res[3:].strip().splitlines()[0].strip() if res.startswith("OK ") and res[3:].strip() else ""
            produced = bool(out) and Path(out).is_file()
            lines.append(f"output={out} exists={produced}")
            ok = ok and produced
    except Exception as e:  # noqa: BLE001
        ok = False
        lines.append(f"error={e!r}")
    finally:
        state["selftest_rc"] = 0 if ok else 1
        head = ("ok" if ok else "FAIL") + f" version={core.VERSION} platform={sys.platform}"
        try:
            Path(out_path).write_text("\n".join([head] + lines + ["done"]) + "\n", encoding="utf-8")
        except Exception:
            pass
        try:
            window.destroy()
        except Exception:
            pass


def run(initial_file: Optional[str] = None, selftest: Optional[str] = None) -> int:
    import webview
    from webview.dom import DOMEventHandler

    html = ui_dir() / "index.html"
    if not html.is_file():
        raise RuntimeError(f"ui/index.html not found ({html})")

    if os.name == "nt":
        renderer = windows_renderer()
        if renderer != "edgechromium":
            # no WebView2 runtime: pywebview would fall back to Internet Explorer, which cannot show the page
            raise ModernUnavailable("webview2", f"renderer={renderer}")

    api = Api()
    window = webview.create_window(
        APP_TITLE, url=str(html), js_api=api,
        width=1040, height=760, min_size=(860, 620),
        background_color="#0f1115", text_select=False,
    )
    api.attach(window)
    state: Dict[str, Any] = {"wired": False, "closed": False, "dead": False, "selftest_rc": 1}

    def on_initialized(renderer=None):
        api.renderer = renderer

    def on_loaded():
        if state["wired"]:
            return
        state["wired"] = True
        try:
            el = window.dom.get_element("#drop")
            if el is not None:
                el.on("drop", DOMEventHandler(api._on_drop, prevent_default=True))
        except Exception:
            pass
        if initial_file:
            api._emit("file", path=str(Path(initial_file).expanduser()))

    def on_closed():
        state["closed"] = True

    def watchdog():
        # WebView2 can fail to initialise (corrupt runtime, unwritable temp folder) and leave a blank
        # window without raising anything; give up after LOAD_TIMEOUT so the launcher opens the classic window.
        deadline = time.monotonic() + LOAD_TIMEOUT
        while time.monotonic() < deadline:
            if window.events.loaded.wait(0.25) or state["closed"]:
                return
        state["dead"] = True
        try:
            window.destroy()
        except Exception:
            pass

    window.events.initialized += on_initialized
    window.events.loaded += on_loaded
    window.events.closed += on_closed

    func, args = None, None
    if selftest:
        func, args = _selftest, (window, api, selftest, initial_file, state)
    elif os.name == "nt":
        func = watchdog
    webview.start(func=func, args=args, debug=bool(os.environ.get("TIKTOKHQ_DEBUG")), private_mode=True)
    if state["dead"]:
        raise ModernUnavailable("noload", f"the page did not load within {LOAD_TIMEOUT:.0f} s")
    return int(state["selftest_rc"]) if selftest else 0
