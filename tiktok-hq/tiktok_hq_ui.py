#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TikTok HQ - modern desktop window.

A native OS web view (pywebview: WebView2 on Windows, WKWebView on macOS)
showing ui/index.html. The page talks to the `Api` class below through
pywebview's js_api bridge; the engine (tiktok_hq.py) is unchanged and runs in a
worker thread, its output lines are queued and the page polls them.

After a successful prepare the page can hand the file to tiktok_hq_upload: it
opens TikTok's upload page in the user's browser, attaches the file, fills the
caption and privacy, and the user presses "Post" there (the app never does).

`Api` has no dependency on pywebview, so it is unit-tested without a display.
`run()` raises ModernUnavailable when the web view cannot be used on this
machine (no WebView2 runtime, or the page never loads); the launcher
(tiktok_hq_gui.py) then opens the classic window instead.
"""
from __future__ import annotations

import collections
import json
import logging
import os
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

import tiktok_hq as core
import tiktok_hq_upload as uploader

APP_TITLE = "TikTok HQ"
VIDEO_FILTER = "Video (*.mp4;*.mov;*.m4v;*.mkv;*.webm;*.avi;*.mts;*.ts;*.MP4;*.MOV)"
ALL_FILTER = "All files (*.*)"
TIME_RE = re.compile(r"time=(\d+):(\d+):(\d+(?:\.\d+)?)")
DONE_RE = re.compile(r"^DONE -> (.+?)\s+\([^()]*\)\s*$")
VERDICTS = ("IDENTICAL", "SAME MEDIA", "SAME PICTURE", "RE-ENCODED")
LOAD_TIMEOUT = 45.0  # seconds the window may stay blank before giving up (Windows)
CAPTION_MAX = 2200
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
    """The folder holding index.html: next to this file, or inside the packaged app (TIKTOKHQ_UI_DIR overrides it)."""
    env = os.environ.get("TIKTOKHQ_UI_DIR")
    if env and (Path(env) / "index.html").is_file():
        return Path(env)
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
        self._uploading = False
        self._upload_cancel: Optional[threading.Event] = None
        self.upload_overrides: Dict[str, Any] = {}  # UploadRequest fields (the self-test points it at the mock page)

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
            "uploading": self._uploading,
        }
        try:
            b = uploader.find_browser()
            d["browser"] = str(b) if b else None
        except Exception:  # noqa: BLE001
            d["browser"] = None
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

    # ----------------------------------------------------------------- upload
    def upload(self, path: str, options: Optional[dict] = None) -> dict:
        """Open TikTok's upload page in the browser, attach `path`, fill caption + privacy; the user posts."""
        try:
            o = options if isinstance(options, dict) else {}
            privacy = str(o.get("privacy") or "only_me")
            if privacy not in uploader.PRIVACY_LABELS:
                return {"started": False, "error": f"unknown privacy {privacy!r}"}
            caption = str(o.get("caption") or "")[:CAPTION_MAX]
            video = Path(str(path)).expanduser()
            if not video.is_file():
                return {"started": False, "error": f"file not found: {video}"}
        except Exception as e:  # noqa: BLE001
            return {"started": False, "error": f"bad options: {e}"}
        with self._lock:
            if self._uploading:
                return {"started": False, "error": "upload_busy"}
            self._uploading = True
            cancel = self._upload_cancel = threading.Event()
        try:
            threading.Thread(target=self._run_upload, args=(str(video), caption, privacy, cancel), daemon=True).start()
        except Exception as e:  # noqa: BLE001
            with self._lock:
                self._uploading = False
            return {"started": False, "error": str(e)}
        return {"started": True}

    def cancel_upload(self) -> bool:
        """Stop driving the browser (before the hand-over) or stop watching for the post (after it)."""
        ev = self._upload_cancel
        if ev is None:
            return False
        ev.set()
        return True

    def _run_upload(self, video: str, caption: str, privacy: str, cancel: threading.Event) -> None:
        self._emit("upload_start", name=Path(video).name, privacy=privacy)

        def status(stage: str, detail: str) -> None:
            self._emit("line", text=f"[upload] {stage} {detail}".rstrip())
            self._emit("upload_stage", stage=stage, detail=detail)

        try:
            req = uploader.UploadRequest(video=video, caption=caption, privacy=privacy, **self.upload_overrides)
            res = uploader.prepare_post(req, status, cancel)
            self._emit("upload_done", state=res.get("state"), privacy=privacy)
        except uploader.Cancelled:
            self._emit("upload_done", state="cancelled", privacy=privacy)
        except uploader.UploadError as e:
            self._emit("line", text=f"[upload] FAILED at {e.step}: {e.message}" + (f" (details: {e.debug_dir})" if e.debug_dir else ""))
            self._emit("upload_done", state="error", step=e.step, message=e.message, debug_dir=e.debug_dir, privacy=privacy)
        except Exception as e:  # noqa: BLE001
            self._emit("line", text="[upload] UNEXPECTED ERROR:\n" + traceback.format_exc())
            self._emit("upload_done", state="error", step="", message=f"{type(e).__name__}: {e}", debug_dir=None, privacy=privacy)
        finally:
            with self._lock:
                self._uploading = False
                if self._upload_cancel is cancel:
                    self._upload_cancel = None

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


RELOAD_AFTER = 6.0   # macOS: seconds without a loaded page before loading it again
RELOAD_TRIES = 3


def _mac_log_navigation_errors() -> None:
    """pywebview's macOS navigation delegate ignores failed loads; add handlers that log them (pywebview logger)."""
    try:
        import objc
        from webview.platforms import cocoa
        log = logging.getLogger("pywebview")

        # an exception escaping into WebKit would end the app: these never raise
        def failed(self, webview, navigation, error):  # noqa: ANN001
            try:
                log.warning(f"page load failed: {error.localizedDescription()} (domain={error.domain()} "
                            f"code={error.code()} url={error.userInfo().get('NSErrorFailingURLStringKey')})")
            except Exception:  # noqa: BLE001
                pass

        def content_died(self, webview):  # noqa: ANN001
            try:
                log.warning("the page's web content process ended")
            except Exception:  # noqa: BLE001
                pass

        delegate = cocoa.BrowserView.BrowserDelegate
        methods = []
        for sel, fn, sig in ((b"webView:didFailProvisionalNavigation:withError:", failed, b"v@:@@@"),
                             (b"webView:didFailNavigation:withError:", failed, b"v@:@@@"),
                             (b"webViewWebContentProcessDidTerminate:", content_died, b"v@:@")):
            if not delegate.instancesRespondToSelector_(sel):
                methods.append(objc.selector(fn, selector=sel, signature=sig))
        if methods:
            objc.classAddMethods(delegate, methods)
    except Exception as e:  # noqa: BLE001
        logging.getLogger("pywebview").debug(f"navigation error logging unavailable: {e!r}")


def _mac_load_guard(window: Any, state: dict) -> None:
    """A first load that fails leaves the macOS window blank for good (pywebview neither reports nor retries it):
    load the page again a few times."""
    if not window.events.shown.wait(60):
        return
    for attempt in range(1, RELOAD_TRIES + 1):
        if window.events.before_load.wait(RELOAD_AFTER) or state.get("closed"):
            return
        logging.getLogger("pywebview").warning(f"page not loaded {attempt * RELOAD_AFTER:.0f} s after the window "
                                               f"opened: loading it again ({window.real_url})")
        state["reloads"] = attempt
        try:
            window.gui.load_url(window.real_url, window.uid)
        except Exception as e:  # noqa: BLE001
            logging.getLogger("pywebview").warning(f"reload failed: {e!r}")


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
UPLOAD_STATE_JS = "(document.getElementById('upload').getAttribute('data-state') || '')"
UPLOAD_TEXT_JS = ("document.getElementById('up-title').textContent + ' | ' + document.getElementById('up-text').textContent")
SELFTEST_CAPTION = "تجربة النشر من TikTok HQ #TikTokHQ"


class _LogTail(logging.Handler):
    """Keeps pywebview's last log lines for the self-test report."""

    def __init__(self, size: int = 80) -> None:
        super().__init__(logging.DEBUG)
        self.lines: "collections.deque[str]" = collections.deque(maxlen=size)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(f"{time.strftime('%H:%M:%S')} {record.levelname} {record.getMessage()}"[:400])
        except Exception:  # noqa: BLE001
            pass


WINDOW_EVENTS = ("initialized", "before_show", "shown", "before_load", "_pywebviewready", "loaded")
PAGE_STATE_JS = ("document.readyState + ' | ' + performance.getEntriesByType('resource').map(function (e) {"
                 " return e.name.split('/').pop() + ':' + Math.round(e.responseEnd); }).join(',')")


def _load_diagnostics(window: Any) -> List[str]:
    """Why a page did not load: web view state, the page's own state, and where every Python thread is."""
    out: List[str] = []
    if sys.platform == "darwin":
        try:
            from PyObjCTools import AppHelper
            from webview.platforms import cocoa
            view = cocoa.BrowserView.instances.get(window.uid)
            box: Dict[str, str] = {}
            answered = threading.Event()

            def read() -> None:
                try:
                    wk = view.webview
                    box["v"] = (f"isLoading={bool(wk.isLoading())} progress={float(wk.estimatedProgress()):.2f} "
                                f"url={wk.URL()} title={wk.title()}")
                except Exception as e:  # noqa: BLE001
                    box["v"] = f"error {e!r}"
                answered.set()

            AppHelper.callAfter(read)
            out.append("webview=" + (box.get("v", "") if answered.wait(10) else "the main thread did not answer in 10 s"))
        except Exception as e:  # noqa: BLE001
            out.append(f"webview=? {e!r}")
    box2: Dict[str, Any] = {}

    def page_state() -> None:
        try:  # straight to the backend: window.run_js would wait for the page to finish loading
            box2["v"] = window.gui.evaluate_js(PAGE_STATE_JS, window.uid, False)
        except Exception as e:  # noqa: BLE001
            box2["v"] = f"error {e!r}"

    t = threading.Thread(target=page_state, daemon=True)
    t.start()
    t.join(10)
    out.append("page_js=" + (str(box2["v"]) if "v" in box2 else "no answer in 10 s"))
    try:
        import faulthandler
        with tempfile.TemporaryFile("w+") as f:
            faulthandler.dump_traceback(file=f, all_threads=True)
            f.seek(0)
            out += ["threads:"] + [f"  {line.rstrip()}" for line in f.read().splitlines()[:80]]
    except Exception as e:  # noqa: BLE001
        out.append(f"threads=? {e!r}")
    return out


def _selftest_upload(window: Any, api: Api, lines: List[str]) -> bool:
    """After the prepare: the page set up the post on the mock page; play the user's Post; the page must notice."""
    st = _js_wait(window, f"['ready','error','closed','cancelled'].indexOf({UPLOAD_STATE_JS}) >= 0 ? {UPLOAD_STATE_JS} : ''", 240)
    lines.append(f"upload_state={st}")
    lines.append("upload_text=" + str(window.evaluate_js(UPLOAD_TEXT_JS) or ""))
    if st != "ready":
        return False
    br = uploader.Browser(Path(api.upload_overrides["profile"]))
    br.port = br._alive_port()
    if not br.port:
        lines.append("upload_browser=not running")
        return False
    cdp = br.page(prefer="mock")
    try:
        pg = uploader.Page(cdp)
        form = pg.js("return window.__form();") or {}
        lines.append("upload_form=" + json.dumps(form, ensure_ascii=False))
        time.sleep(2.0)  # the app must leave the Post button alone
        before = pg.js("return window.__events;") or []
        pg.js("return window.__simulateUserPost();")  # stands in for the user's own click on Post
    finally:
        cdp.close()
    posted = _js_wait(window, f"{UPLOAD_STATE_JS} === 'posted' ? 'posted' : ''", 60)
    lines.append(f"upload_after_post={posted or window.evaluate_js(UPLOAD_STATE_JS)}")
    checks = {
        "caption": uploader._norm(form.get("caption", "")) == uploader._norm(SELFTEST_CAPTION),
        "privacy": form.get("privacy") == "Only you",
        "file": bool(form.get("file")) and str(form.get("file", "")).endswith("_TikTokHQ.mp4"),
        "post_untouched": "post-click" not in before,
        "noticed": posted == "posted",
    }
    lines.append("upload_checks=" + " ".join(f"{k}:{'ok' if v else 'FAIL'}" for k, v in checks.items()))
    return all(checks.values())


def _selftest(window: Any, api: Api, out_path: str, initial_file: Optional[str], state: dict,
              upload_mock: Optional[str] = None) -> None:
    """CI: drive the real window (bridge, RTL page, file card, Prepare, and with upload_mock the hand-over to
    the browser on the mock TikTok page) and write the results to out_path."""
    lines: List[str] = []
    ok = True
    t0 = time.monotonic()
    try:
        if not window.events.loaded.wait(60):
            # where it stopped: before_load is set once the page finished loading, loaded once the bridge is in
            ev = window.events
            lines.append("window_events=" + " ".join(f"{n}:{int(getattr(ev, n).is_set())}" for n in WINDOW_EVENTS))
            page_url = str(getattr(window, "real_url", "") or "")
            try:
                import urllib.request
                with urllib.request.urlopen(page_url, timeout=5) as r:  # noqa: S310  (pywebview's 127.0.0.1 server)
                    lines.append(f"page_server={r.status} {len(r.read())} bytes ({page_url})")
            except Exception as e:  # noqa: BLE001
                lines.append(f"page_server=ERROR {type(e).__name__}: {e} ({page_url})")
            lines += _load_diagnostics(window)
            late = ev.loaded.wait(45)
            lines.append("loaded_later=" + (f"after {time.monotonic() - t0:.0f} s" if late else "no"))
            raise RuntimeError("the page did not load within 60 s")
        lines.append(f"load_seconds={time.monotonic() - t0:.1f} reloads={state.get('reloads', 0)}")
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
            if upload_mock:
                window.evaluate_js("document.getElementById('auto-upload').checked = true;"
                                   "document.querySelector('#privacy .seg[data-value=\"only_me\"]').click();"
                                   "document.getElementById('caption').value = " + json.dumps(SELFTEST_CAPTION) + ";")
            else:  # a self-test never opens the real TikTok
                window.evaluate_js("document.getElementById('auto-upload').checked = false;")
            window.evaluate_js("document.getElementById('btn-prepare').click()")
            res = str(_js_wait(window, RESULT_JS, 300) or "")
            lines.append("prepare=" + res.replace("\n", " | "))
            out = res[3:].strip().splitlines()[0].strip() if res.startswith("OK ") and res[3:].strip() else ""
            produced = bool(out) and Path(out).is_file()
            lines.append(f"output={out} exists={produced}")
            ok = ok and produced
            if upload_mock:
                ok = _selftest_upload(window, api, lines) and ok
    except Exception as e:  # noqa: BLE001
        ok = False
        lines.append(f"error={e!r}")
    finally:
        if upload_mock and api.upload_overrides.get("profile"):
            try:
                uploader.Browser(Path(api.upload_overrides["profile"])).close()
            except Exception:  # noqa: BLE001
                pass
        state["selftest_rc"] = 0 if ok else 1
        tail = state.get("log_tail")
        if tail is not None:
            shown = list(tail.lines) if not ok else [ln for ln in tail.lines if " WARNING " in ln or " ERROR " in ln]
            if shown:
                lines += ["pywebview log:"] + [f"  {line}" for line in shown]
        head = ("ok" if ok else "FAIL") + f" version={core.VERSION} platform={sys.platform}"
        try:
            Path(out_path).write_text("\n".join([head] + lines + ["done"]) + "\n", encoding="utf-8")
        except Exception:
            pass
        try:
            window.destroy()
        except Exception:
            pass


def run(initial_file: Optional[str] = None, selftest: Optional[str] = None, upload_mock: Optional[str] = None) -> int:
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
    state: Dict[str, Any] = {"wired": False, "closed": False, "dead": False, "selftest_rc": 1}
    if selftest:
        tail = _LogTail()
        wv_log = logging.getLogger("pywebview")
        wv_log.addHandler(tail)
        wv_log.setLevel(logging.DEBUG)
        state["log_tail"] = tail
    scratch: Optional[tempfile.TemporaryDirectory] = None
    if selftest and upload_mock:
        # the self-test posts to the mock TikTok page with a throw-away browser profile
        scratch = tempfile.TemporaryDirectory(prefix="tiktokhq_selftest_", ignore_cleanup_errors=True)
        api.upload_overrides = {
            "url": Path(upload_mock).resolve().as_uri(), "mock": True, "profile": str(Path(scratch.name) / "profile"),
            "login_timeout": 60.0, "upload_timeout": 120.0, "watch_timeout": 120.0,
            "debug_root": str(Path(selftest).resolve().parent / "upload-debug"),
        }
    window = webview.create_window(
        APP_TITLE, url=str(html), js_api=api,
        width=1040, height=760, min_size=(860, 620),
        background_color="#0f1115", text_select=False,
    )
    api.attach(window)

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

    if sys.platform == "darwin":
        _mac_log_navigation_errors()
        threading.Thread(target=_mac_load_guard, args=(window, state), daemon=True).start()

    func, args = None, None
    if selftest:
        func, args = _selftest, (window, api, selftest, initial_file, state, upload_mock)
    elif os.name == "nt":
        func = watchdog
    try:
        webview.start(func=func, args=args, debug=bool(os.environ.get("TIKTOKHQ_DEBUG")), private_mode=True)
    finally:
        if scratch is not None:
            scratch.cleanup()
    if state["dead"]:
        raise ModernUnavailable("noload", f"the page did not load within {LOAD_TIMEOUT:.0f} s")
    return int(state["selftest_rc"]) if selftest else 0
