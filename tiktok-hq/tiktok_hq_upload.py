#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TikTok HQ - get a prepared video onto TikTok through the user's own browser.

TikTok's official Content Posting API cannot be used here: an app that has not
passed TikTok's audit may only post to private accounts. So this module drives
the TikTok Studio upload page in a real Microsoft Edge / Google Chrome window,
the same page a person uses (and the upload path the "original quality" method
is built for). It uses a dedicated browser profile: the user logs in once in
that window and the session is kept for later uploads.

It is semi-automatic on purpose: the app attaches the file, waits for TikTok to
receive it, writes the caption and sets the privacy, then hands over. The user
reviews the form and presses "Post" themselves. The app never presses Post (or
"Post now"); after the hand-over it only reads the page to tell whether the
post went through.

Control goes through the Chrome DevTools Protocol with a tiny standard-library
websocket client (no Selenium/Playwright, nothing extra to install). Nothing is
spoofed: it is a normal browser window started with remote debugging on a
private profile.

Safety rules:
  * the caption and the privacy setting are read back after filling them; if
    either cannot be confirmed the app stops before the hand-over and says so;
  * the app clicks nothing while the user logs in, and nothing after the hand-over;
  * any failure leaves the browser open on the form and saves a screenshot +
    page copy for support.

  python tiktok_hq_upload.py VIDEO [--caption TEXT] [--privacy only_me|friends|everyone]
"""
from __future__ import annotations

import base64
import json
import os
import socket
import struct
import subprocess
import sys
import threading
import time
import unicodedata
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

UPLOAD_URL = "https://www.tiktok.com/tiktokstudio/upload?from=webapp&lang=en"
UPLOAD_URL_FALLBACK = "https://www.tiktok.com/creator-center/upload?lang=en"
PRIVACY_LABELS: Dict[str, List[str]] = {
    "only_me": ["Only you", "Only me", "Private", "أنت فقط", "أنا فقط", "فقط أنت", "خاص"],
    "friends": ["Friends", "الأصدقاء"],
    "everyone": ["Everyone", "Public", "الجميع", "الكل", "عام"],
}
FILE_INPUT = ['input[type=file][accept*="video"]', "input[type=file]"]
CAPTION = ['.public-DraftEditor-content[contenteditable="true"]', 'div[contenteditable="true"]']
VISIBILITY = ['[data-e2e="video_visibility_container"] button[role="combobox"]',
              '[data-e2e="video_visibility_container"] [role="combobox"]',
              '[data-e2e="video_visibility_container"] button']
# the TikTok post button (found to check it is ready and to point the user at it; never clicked).
# The fallback only accepts a primary button whose whole label is "Post".
POST_BUTTON_JS = ("(H.q(['button[data-e2e=\"post_video_button\"]'], true) || "
                  "H.byText('button.TUXButton--primary', ['Post', 'نشر', 'انشر'], true))")
MARK_ATTR = "data-tiktokhq-ready"
DISMISS_TEXTS = ["Not now", "Got it", "Skip", "Decline optional cookies", "ليس الآن", "حسنًا", "تخطي"]
# shown only after a post went through (a sidebar link such as "Posts" must never count)
SUCCESS_TEXTS = ["Your video has been uploaded", "Video published", "视频已发布", "Your video is being posted",
                 "Your video has been posted", "تم نشر الفيديو"]
# status(stage, detail) stages, in order; the run ends with one of WATCH_RESULTS
STAGES = ("browser", "login", "attach", "uploading", "details", "ready")
WATCH_RESULTS = ("posted", "not_seen", "closed", "stopped")


class UploadError(Exception):
    """An upload step failed. `step` is one of STAGES; `debug_dir` holds a screenshot + page copy if saved."""

    def __init__(self, step: str, message: str, debug_dir: Optional[str] = None) -> None:
        super().__init__(message)
        self.step = step
        self.message = message
        self.debug_dir = debug_dir


class Cancelled(Exception):
    pass


# --------------------------------------------------------------------------- #
# minimal websocket client (RFC 6455, client side, text frames)
# --------------------------------------------------------------------------- #

def _mask(data: bytes, key: bytes) -> bytes:
    if not data:
        return data
    k = (key * (len(data) // 4 + 1))[: len(data)]
    return (int.from_bytes(data, "big") ^ int.from_bytes(k, "big")).to_bytes(len(data), "big")


def encode_frame(text: str, opcode: int = 0x1, key: Optional[bytes] = None) -> bytes:
    payload = text.encode("utf-8") if isinstance(text, str) else bytes(text)
    key = key or os.urandom(4)
    n = len(payload)
    head = bytearray([0x80 | opcode])
    if n < 126:
        head.append(0x80 | n)
    elif n < 65536:
        head.append(0x80 | 126)
        head += struct.pack(">H", n)
    else:
        head.append(0x80 | 127)
        head += struct.pack(">Q", n)
    return bytes(head) + key + _mask(payload, key)


def parse_frame(buf: bytes):
    """Return (fin, opcode, payload, consumed) for the first complete frame in buf, or None if incomplete."""
    if len(buf) < 2:
        return None
    b1, b2 = buf[0], buf[1]
    fin, opcode, masked, n = bool(b1 & 0x80), b1 & 0x0F, bool(b2 & 0x80), b2 & 0x7F
    pos = 2
    if n == 126:
        if len(buf) < 4:
            return None
        n = struct.unpack(">H", buf[2:4])[0]
        pos = 4
    elif n == 127:
        if len(buf) < 10:
            return None
        n = struct.unpack(">Q", buf[2:10])[0]
        pos = 10
    key = b""
    if masked:
        if len(buf) < pos + 4:
            return None
        key = buf[pos:pos + 4]
        pos += 4
    if len(buf) < pos + n:
        return None
    payload = buf[pos:pos + n]
    if masked:
        payload = _mask(payload, key)
    return fin, opcode, payload, pos + n


class WebSocket:
    def __init__(self, url: str, timeout: float = 15.0) -> None:
        u = urllib.parse.urlparse(url)
        self.sock = socket.create_connection((u.hostname, u.port or 80), timeout=timeout)
        key = base64.b64encode(os.urandom(16)).decode()
        path = u.path + (("?" + u.query) if u.query else "")
        self.sock.sendall((f"GET {path} HTTP/1.1\r\nHost: {u.hostname}:{u.port}\r\nUpgrade: websocket\r\n"
                           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("websocket handshake failed")
            resp += chunk
        head, _, rest = resp.partition(b"\r\n\r\n")
        if b" 101" not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError("websocket handshake refused: " + head.split(b"\r\n", 1)[0].decode("latin-1"))
        self.buf = rest
        self.lock = threading.Lock()

    def send(self, text: str) -> None:
        with self.lock:
            self.sock.sendall(encode_frame(text))

    def recv(self, timeout: float) -> str:
        deadline = time.monotonic() + max(0.01, timeout)
        parts: List[bytes] = []
        while True:
            fr = parse_frame(self.buf)
            if fr is None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("no message")
                self.sock.settimeout(remaining)
                try:
                    chunk = self.sock.recv(1 << 16)
                except socket.timeout:
                    raise TimeoutError("no message")
                if not chunk:
                    raise ConnectionError("browser connection closed")
                self.buf += chunk
                continue
            fin, opcode, payload, used = fr
            self.buf = self.buf[used:]
            if opcode == 0x9:  # ping
                with self.lock:
                    self.sock.sendall(encode_frame(payload.decode("latin-1"), opcode=0xA))
                continue
            if opcode == 0x8:
                raise ConnectionError("browser connection closed")
            if opcode in (0x0, 0x1, 0x2):
                parts.append(payload)
                if fin:
                    return b"".join(parts).decode("utf-8", "replace")

    def close(self) -> None:
        try:
            self.sock.close()
        except Exception:
            pass


class CDP:
    """Chrome DevTools Protocol over one page websocket."""

    def __init__(self, ws_url: str) -> None:
        self.ws = WebSocket(ws_url)
        self.next_id = 0

    def call(self, method: str, params: Optional[dict] = None, timeout: float = 30.0) -> dict:
        self.next_id += 1
        mid = self.next_id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            msg = json.loads(self.ws.recv(deadline - time.monotonic()))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error'].get('message')}")
                return msg.get("result", {})

    def close(self) -> None:
        self.ws.close()


# --------------------------------------------------------------------------- #
# browser discovery and launch
# --------------------------------------------------------------------------- #

def app_data_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return base / "TikTokHQ"


def browser_candidates() -> List[Path]:
    env = os.environ.get("TIKTOKHQ_BROWSER")
    out: List[Path] = [Path(env)] if env else []
    if os.name == "nt":
        roots = [os.environ.get(k, "") for k in ("PROGRAMFILES(X86)", "PROGRAMFILES", "LOCALAPPDATA")]
        for rel in (r"Microsoft\Edge\Application\msedge.exe", r"Google\Chrome\Application\chrome.exe",
                    r"BraveSoftware\Brave-Browser\Application\brave.exe"):
            out += [Path(r) / rel for r in roots if r]
    elif sys.platform == "darwin":
        for app, exe in (("Google Chrome", "Google Chrome"), ("Microsoft Edge", "Microsoft Edge"),
                         ("Brave Browser", "Brave Browser"), ("Chromium", "Chromium")):
            for base in (Path("/Applications"), Path.home() / "Applications"):
                out.append(base / f"{app}.app" / "Contents" / "MacOS" / exe)
    else:
        import shutil
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge"):
            p = shutil.which(name)
            if p:
                out.append(Path(p))
    return out


def find_browser() -> Optional[Path]:
    for p in browser_candidates():
        if p.is_file():
            return p
    return None


def _http_json(url: str, method: str = "GET", timeout: float = 3.0) -> Any:
    req = urllib.request.Request(url, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310  (127.0.0.1 only)
        return json.loads(r.read().decode("utf-8"))


class Browser:
    """A Chromium-family browser on a dedicated profile with remote debugging on 127.0.0.1."""

    def __init__(self, profile: Path) -> None:
        self.profile = profile
        self.port: Optional[int] = None
        self.proc: Optional[subprocess.Popen] = None

    def _alive_port(self) -> Optional[int]:
        f = self.profile / "DevToolsActivePort"
        try:
            port = int(f.read_text(encoding="utf-8").splitlines()[0].strip())
            _http_json(f"http://127.0.0.1:{port}/json/version", timeout=1.5)
            return port
        except Exception:
            return None

    def start(self, url: str, timeout: float = 40.0) -> bool:
        """Start the browser on `url`. Returns True when it was already running (window left from a previous upload)."""
        self.profile.mkdir(parents=True, exist_ok=True)
        port = self._alive_port()
        if port:
            self.port = port
            return True
        exe = find_browser()
        if not exe:
            raise UploadError("browser", "no_browser")
        try:
            (self.profile / "DevToolsActivePort").unlink()
        except Exception:
            pass
        args = [str(exe), f"--user-data-dir={self.profile}", "--remote-debugging-port=0",
                "--no-first-run", "--no-default-browser-check", "--new-window"]
        args += [a for a in os.environ.get("TIKTOKHQ_BROWSER_ARGS", "").split() if a]
        args.append(url)
        kw: Dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
        if os.name == "nt":
            kw["creationflags"] = 0x00000200 | 0x08000000  # CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW
        else:
            kw["start_new_session"] = True
        self.proc = subprocess.Popen(args, **kw)
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            port = self._alive_port()
            if port:
                self.port = port
                return False
            if self.proc.poll() is not None and self.proc.returncode not in (0, None):
                break
            time.sleep(0.25)
        raise UploadError("browser", f"the browser did not start ({exe.name})")

    def close(self, wait: float = 10.0) -> None:
        """Close the whole browser (used by the self-test; normal uploads leave the window open)."""
        port = self.port or self._alive_port()
        if port:
            try:
                ws = _http_json(f"http://127.0.0.1:{port}/json/version").get("webSocketDebuggerUrl")
                cdp = CDP(ws)
                try:
                    cdp.call("Browser.close", timeout=5)
                except Exception:
                    pass
                cdp.close()
            except Exception:
                pass
        deadline = time.monotonic() + wait
        while time.monotonic() < deadline and self._alive_port():
            time.sleep(0.25)
        if self.proc is not None:
            try:
                self.proc.wait(timeout=max(0.1, deadline - time.monotonic()))
            except Exception:
                pass

    def _targets(self) -> List[dict]:
        assert self.port
        targets = [t for t in _http_json(f"http://127.0.0.1:{self.port}/json/list") if t.get("type") == "page"]
        return [t for t in targets if not str(t.get("url", "")).startswith(("devtools://", "chrome-extension://"))]

    @staticmethod
    def _connect(target: dict) -> CDP:
        cdp = CDP(target["webSocketDebuggerUrl"])
        try:
            cdp.call("Page.bringToFront")
        except Exception:
            pass
        return cdp

    def page(self, prefer: str = "tiktok", wait: float = 5.0) -> CDP:
        """Connect to the tab whose address contains `prefer` (waits a little for it), else to any tab."""
        deadline = time.monotonic() + wait
        targets = self._targets()
        while not any(prefer in str(t.get("url", "")) for t in targets) and time.monotonic() < deadline:
            time.sleep(0.25)
            targets = self._targets()
        targets.sort(key=lambda t: prefer not in str(t.get("url", "")))
        if not targets:
            targets = [_http_json(f"http://127.0.0.1:{self.port}/json/new?about:blank", method="PUT")]
        return self._connect(targets[0])

    def new_page(self, url: str) -> CDP:
        """Open `url` in a new tab (an earlier tab may hold a form the user has not finished: never navigate it away)."""
        assert self.port
        # encoded: unencoded, the browser cuts the address at the first "&" (TikTok's "&lang=en" would be lost)
        quoted = urllib.parse.quote(url, safe="")
        return self._connect(_http_json(f"http://127.0.0.1:{self.port}/json/new?{quoted}", method="PUT"))


# --------------------------------------------------------------------------- #
# page helpers
# --------------------------------------------------------------------------- #

JS_PRELUDE = r"""
const H = {
  docs() { const out = [document]; const walk = (d) => { for (const f of d.querySelectorAll('iframe,frame')) { try { const cd = f.contentDocument; if (cd && out.indexOf(cd) < 0) { out.push(cd); walk(cd); } } catch (e) {} } }; walk(document); return out; },
  norm(t) { return String(t || '').replace(/[​-‍﻿]/g, '').replace(/\s+/g, ' ').trim().toLowerCase(); },
  visible(el) { if (!el || !el.isConnected) return false; const r = el.getBoundingClientRect(); if (r.width < 1 || r.height < 1) return false; const st = el.ownerDocument.defaultView.getComputedStyle(el); return st.visibility !== 'hidden' && st.display !== 'none' && st.opacity !== '0'; },
  q(sels, vis) { for (const d of H.docs()) { for (const s of sels) { let list; try { list = d.querySelectorAll(s); } catch (e) { continue; } for (const el of list) { if (!vis || H.visible(el)) return el; } } } return null; },
  byText(sel, texts, exact) { const ts = texts.map(H.norm); for (const d of H.docs()) { for (const el of d.querySelectorAll(sel)) { if (!H.visible(el)) continue; const tx = H.norm(el.innerText || el.textContent); for (const t of ts) { if (exact ? tx === t : tx.indexOf(t) >= 0) return el; } } } return null; },
  enabled(el) { return !!el && !el.disabled && el.getAttribute('data-disabled') !== 'true' && el.getAttribute('aria-disabled') !== 'true'; },
  text(el) { return el ? String(el.innerText || el.textContent || '') : ''; },
  rendered(el) {
    /* true when no ancestor hides the element (the element itself may be display:none, like file inputs) */
    for (let a = el.parentElement; a; a = a.parentElement) {
      const st = a.ownerDocument.defaultView.getComputedStyle(a);
      if (st.display === 'none' || st.visibility === 'hidden') return false;
    }
    return true;
  },
  fileInput(sels) {
    /* the upload field is a hidden <input type=file>; it only counts once its drop area is shown */
    for (const d of H.docs()) { for (const s of sels) { for (const el of d.querySelectorAll(s)) {
      if (H.rendered(el)) return el;
    } } }
    return null;
  },
  hasText(texts) {
    /* a visible text node containing one of texts */
    const ts = texts.map(H.norm);
    for (const d of H.docs()) {
      if (!d.body) continue;
      const w = d.createTreeWalker(d.body, NodeFilter.SHOW_TEXT);
      for (let n = w.nextNode(); n; n = w.nextNode()) {
        if (!n.data || n.data.length < 6) continue;
        const tx = H.norm(n.data);
        for (const t of ts) { if (tx.indexOf(t) >= 0 && H.visible(n.parentElement)) return true; }
      }
    }
    return false;
  },
  point(el) {
    el.scrollIntoView({block: 'center', inline: 'center'});
    const r = el.getBoundingClientRect();
    let x = r.left + r.width / 2, y = r.top + r.height / 2;
    let w = el.ownerDocument.defaultView;
    while (w && w !== w.top && w.frameElement) { const fr = w.frameElement.getBoundingClientRect(); x += fr.left; y += fr.top; w = w.parent; }
    const t = document.elementFromPoint(x, y);
    const covered = !t ? true : (t.tagName === 'IFRAME' || t.tagName === 'FRAME') ? false : !(t === el || el.contains(t) || t.contains(el));
    return {found: true, x: x, y: y, covered: covered};
  },
};
"""


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFC", str(t or ""))
    for z in ("​", "‌", "‍", "﻿"):
        t = t.replace(z, "")
    return " ".join(t.split())


class Page:
    def __init__(self, cdp: CDP, cancel: Optional[threading.Event] = None) -> None:
        self.cdp = cdp
        self.cancel = cancel or threading.Event()
        self.mac = sys.platform == "darwin"

    def check_cancel(self) -> None:
        if self.cancel.is_set():
            raise Cancelled()

    def js(self, body: str, timeout: float = 20.0) -> Any:
        """Run `body` (a function body using H, ending in `return ...`) and return its JSON value."""
        expr = "(() => {" + JS_PRELUDE + body + "})()"
        r = self.cdp.call("Runtime.evaluate", {"expression": expr, "returnByValue": True, "userGesture": True}, timeout)
        if r.get("exceptionDetails"):
            d = r["exceptionDetails"]
            raise RuntimeError("page script error: " + str((d.get("exception") or {}).get("description") or d.get("text")))
        return (r.get("result") or {}).get("value")

    def try_js(self, body: str, default: Any = None, timeout: float = 20.0) -> Any:
        """js() for polling loops: a busy or navigating page gives `default` (a closed browser still raises)."""
        try:
            return self.js(body, timeout)
        except (TimeoutError, RuntimeError):
            return default

    def url(self) -> str:
        try:
            return str(self.js("return location.href;"))
        except Exception:
            return ""

    def front(self) -> None:
        """Show the browser window (restored if minimised) on this tab."""
        try:
            w = self.cdp.call("Browser.getWindowForTarget", timeout=5)
            if (w.get("bounds") or {}).get("windowState") == "minimized":
                self.cdp.call("Browser.setWindowBounds", {"windowId": w["windowId"], "bounds": {"windowState": "normal"}}, timeout=5)
        except Exception:
            pass
        try:
            self.cdp.call("Page.bringToFront", timeout=5)
        except Exception:
            pass

    def mark_post_button(self) -> bool:
        """Scroll TikTok's Post button into view and outline it so the user sees where to press (not clicked)."""
        return bool(self.try_js(
            "const b = " + POST_BUTTON_JS + "; if (!b) return false;"
            "b.setAttribute('" + MARK_ATTR + "', '1'); const d = b.ownerDocument;"
            "if (!d.getElementById('tiktokhq-ready-style')) { const st = d.createElement('style'); st.id = 'tiktokhq-ready-style';"
            "st.textContent = '[" + MARK_ATTR + "]{outline:3px solid #fe2c55 !important;outline-offset:3px !important}';"
            "(d.head || d.documentElement).appendChild(st); }"
            "b.scrollIntoView({block: 'center', inline: 'center'}); return true;", False))

    def navigate(self, url: str) -> None:
        self.cdp.call("Page.navigate", {"url": url})

    def wait(self, fn: Callable[[], Any], timeout: float, interval: float = 0.5, what: str = "") -> Any:
        deadline = time.monotonic() + timeout
        while True:
            self.check_cancel()
            try:
                v = fn()
            except (TimeoutError, RuntimeError):
                v = None
            if v:
                return v
            if time.monotonic() >= deadline:
                raise TimeoutError(what or "timed out")
            time.sleep(interval)

    # -------------------------------------------------------------- input
    def mouse_click(self, x: float, y: float) -> None:
        self.cdp.call("Input.dispatchMouseEvent", {"type": "mouseMoved", "x": x, "y": y, "button": "none"})
        for typ in ("mousePressed", "mouseReleased"):
            self.cdp.call("Input.dispatchMouseEvent", {"type": typ, "x": x, "y": y, "button": "left", "clickCount": 1})

    def key(self, key: str, code: str, vk: int, modifiers: int = 0, commands: Optional[List[str]] = None) -> None:
        base = {"key": key, "code": code, "windowsVirtualKeyCode": vk, "nativeVirtualKeyCode": vk, "modifiers": modifiers}
        down = dict(base, type="rawKeyDown")
        if commands:
            down["commands"] = commands
        self.cdp.call("Input.dispatchKeyEvent", down)
        self.cdp.call("Input.dispatchKeyEvent", dict(base, type="keyUp"))

    def click(self, find_js: str) -> bool:
        """find_js: expression producing an element or null. Real mouse click on it (dismissing overlays first)."""
        for attempt in range(3):
            pt = self.js(f"const el = {find_js}; return el ? H.point(el) : {{found: false}};")
            if not pt or not pt.get("found"):
                return False
            if pt.get("covered") and attempt < 2:
                self.dismiss_popups()
                time.sleep(0.4)
                continue
            if pt.get("covered"):
                # still covered: fall back to a DOM click on the element itself
                return bool(self.js(f"const el = {find_js}; if (!el) return false; el.click(); return true;"))
            self.mouse_click(pt["x"], pt["y"])
            return True
        return False

    def dismiss_popups(self) -> int:
        """Close cookie banners and 'Not now' style pop-ups. Never clicks anything that cancels or posts."""
        n = 0
        for _ in range(3):
            found = self.try_js("const el = H.byText('button', " + json.dumps(DISMISS_TEXTS) + ", true); "
                                "return el ? H.point(el) : null;")
            if not found:
                break
            self.mouse_click(found["x"], found["y"])
            n += 1
            time.sleep(0.4)
        return n

    def screenshot_and_html(self, folder: Path) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        try:
            shot = self.cdp.call("Page.captureScreenshot", {"format": "png"}, timeout=20)
            (folder / "screen.png").write_bytes(base64.b64decode(shot.get("data", "")))
        except Exception:
            pass
        try:
            html = self.js("return document.documentElement.outerHTML.slice(0, 3000000);", timeout=20)
            (folder / "page.html").write_text(str(html or ""), encoding="utf-8")
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# the upload flow
# --------------------------------------------------------------------------- #

@dataclass
class UploadRequest:
    video: str
    caption: str = ""
    privacy: str = "only_me"
    url: str = UPLOAD_URL
    profile: Optional[str] = None
    login_timeout: float = 600.0     # the user logs in once in the browser window
    upload_timeout: float = 1800.0   # big files on slow connections
    watch_timeout: float = 1800.0    # how long to wait for the user to press Post
    mock: bool = False               # tests / CI self-test against tests/fixtures/mock_studio.html
    debug_root: Optional[str] = None
    log: List[str] = field(default_factory=list)


def _logged_in(page: Page) -> bool:
    try:
        cookies = page.cdp.call("Network.getCookies", {"urls": ["https://www.tiktok.com"]}).get("cookies", [])
        return any(c.get("name") in ("sessionid", "sessionid_ss") and c.get("value") for c in cookies)
    except (TimeoutError, RuntimeError):
        return False


def prepare_post(req: UploadRequest, status: Callable[[str, str], None],
                 cancel: Optional[threading.Event] = None) -> dict:
    """Fill TikTok's upload form (file, caption, privacy) and hand over: the user presses Post.

    status(stage, detail) is called as it progresses (STAGES, then "ready" at the hand-over). After the
    hand-over the page is only read, never clicked, until the post shows up, the window closes, `cancel`
    is set or req.watch_timeout passes. Returns {"state": one of WATCH_RESULTS, ...}.
    Raises UploadError, or Cancelled when `cancel` is set before the hand-over.
    """
    video = Path(req.video).expanduser().resolve()
    if not video.is_file():
        raise UploadError("attach", f"file not found: {video}")
    if req.privacy not in PRIVACY_LABELS:
        raise UploadError("details", f"unknown privacy {req.privacy!r}")
    profile = Path(req.profile) if req.profile else app_data_dir() / "browser"
    debug_root = Path(req.debug_root) if req.debug_root else app_data_dir() / "upload-debug"
    prefer = "mock" if req.mock else "tiktok"
    step = "browser"
    page: Optional[Page] = None

    def say(stage: str, detail: str = "") -> None:
        req.log.append(f"{time.strftime('%H:%M:%S')} {stage} {detail}".strip())
        status(stage, detail)

    try:
        say("browser")
        br = Browser(profile)
        if br.start(req.url):
            # the window from an earlier upload is still open: a new tab, the old one is left as it is
            page = Page(br.new_page(req.url), cancel)
        else:
            page = Page(br.page(prefer=prefer), cancel)
            if prefer not in page.url():
                page.navigate(req.url)
        time.sleep(1.0)

        # ---- login: wait until the upload form (file input) is there. Nothing is clicked here:
        # the user may be typing a password or solving a check in this window.
        step = "login"
        say("login")
        deadline = time.monotonic() + req.login_timeout
        last_nav = time.monotonic()
        navs = 0
        asked = False
        while True:
            page.check_cancel()
            if page.try_js("return !!H.fileInput(" + json.dumps(FILE_INPUT) + ");"):
                break
            if time.monotonic() > deadline:
                raise UploadError("login", "login_timeout")
            if not req.mock:
                logged = _logged_in(page)
                if not logged and not asked:
                    say("login", "login_needed")
                    page.front()
                    asked = True
                if logged and "/upload" not in page.url() and time.monotonic() - last_nav > 12:
                    # after logging in TikTok usually lands on the home page: go back to the upload form
                    navs += 1
                    page.navigate(UPLOAD_URL_FALLBACK if navs % 3 == 0 else req.url)
                    last_nav = time.monotonic()
            time.sleep(1.0)

        # ---- attach the file
        step = "attach"
        say("attach", video.name)
        r = page.cdp.call("Runtime.evaluate", {"expression": "(() => {" + JS_PRELUDE + "return H.fileInput(" + json.dumps(FILE_INPUT) + ");})()",
                                               "returnByValue": False})
        oid = (r.get("result") or {}).get("objectId")
        if not oid:
            raise UploadError("attach", "upload field not found")
        page.cdp.call("DOM.setFileInputFiles", {"files": [str(video)], "objectId": oid})

        # ---- wait for TikTok to finish receiving the file
        step = "uploading"
        say("uploading")
        deadline = time.monotonic() + req.upload_timeout
        last_pct = ""
        while True:
            page.check_cancel()
            st = page.try_js(
                "const b = " + POST_BUTTON_JS + ";"
                "let pct = ''; const m = (document.body.innerText || '').match(/(\\d{1,3})%/); if (m) pct = m[1];"
                "return {found: !!b, enabled: H.enabled(b), pct: pct, editor: !!H.q(" + json.dumps(CAPTION) + ", true)};")
            if st and st.get("found") and st.get("enabled") and st.get("editor"):
                break
            if st and st.get("pct") and st["pct"] != last_pct:
                last_pct = st["pct"]
                say("uploading", f"{last_pct}%")
            if time.monotonic() > deadline:
                raise UploadError("uploading", "upload_timeout")
            page.dismiss_popups()
            time.sleep(1.0)
        time.sleep(1.0)
        page.dismiss_popups()

        # ---- caption (read back)
        step = "details"
        say("details", "caption")
        want = _norm(req.caption)
        ok_caption = False
        for attempt in range(2):
            page.check_cancel()
            if not page.click("H.q(" + json.dumps(CAPTION) + ", true)"):
                raise UploadError("details", "caption field not found")
            time.sleep(0.3)
            if attempt == 0:
                page.key("a", "KeyA", 65, modifiers=4 if page.mac else 2, commands=["selectAll"])
                page.key("Backspace", "Backspace", 8, commands=["deleteBackward"])
                if want:
                    page.cdp.call("Input.insertText", {"text": req.caption})
            else:  # second try: editing commands inside the page
                page.js("const el = H.q(" + json.dumps(CAPTION) + ", true); el.focus(); document.execCommand('selectAll');"
                        "document.execCommand('delete'); " + ("document.execCommand('insertText', false, " + json.dumps(req.caption) + ");" if want else "") +
                        "return true;")
            time.sleep(0.6)
            got = _norm(page.try_js("return H.text(H.q(" + json.dumps(CAPTION) + ", true));", "") or "")
            if got == want:
                ok_caption = True
                break
        if not ok_caption:
            raise UploadError("details", "caption_mismatch")
        # leave the editor (closes a hashtag suggestion list that could cover the next field)
        page.try_js("const a = document.activeElement; if (a && a.blur) a.blur(); return true;")
        time.sleep(0.3)

        # ---- privacy (read back; no hand-over if it cannot be confirmed)
        say("details", "privacy")
        labels = PRIVACY_LABELS[req.privacy]

        def privacy_ok() -> bool:
            cur = page.try_js("return H.norm(H.text(H.q(" + json.dumps(VISIBILITY) + ", true)));", "") or ""
            return any(_norm(l).lower() in cur for l in labels)

        if not privacy_ok():
            for attempt in range(2):
                page.check_cancel()
                if not page.click("H.q(" + json.dumps(VISIBILITY) + ", true)"):
                    raise UploadError("details", "privacy_not_found")
                try:
                    page.wait(lambda: page.js("return !!H.byText('[role=option]', " + json.dumps(labels) + ", false);"),
                              timeout=6, interval=0.3)
                except TimeoutError:
                    continue
                page.click("H.byText('[role=option]', " + json.dumps(labels) + ", false)")
                time.sleep(0.6)
                if privacy_ok():
                    break
        if not privacy_ok():
            raise UploadError("details", "privacy_not_confirmed")

        # ---- hand over: the user reviews the form and presses Post
        step = "ready"
        page.check_cancel()
        page.dismiss_popups()
        if not page.try_js("return H.enabled(" + POST_BUTTON_JS + ");"):
            raise UploadError("ready", "post_button_unavailable")
        page.mark_post_button()
        page.front()
        say("ready")
        state = _watch(page, req.watch_timeout)
        say(state)
        return {"ok": True, "state": state, "privacy": req.privacy, "video": str(video)}
    except Cancelled:
        raise
    except UploadError as e:
        e.debug_dir = e.debug_dir or _save_debug(page, debug_root, req, e.step, e.message)
        raise
    except ConnectionError:  # the window or the tab was closed
        raise UploadError(step, "browser_closed", _save_debug(None, debug_root, req, step, "browser_closed"))
    except Exception as e:  # noqa: BLE001
        msg = f"{type(e).__name__}: {e}"
        raise UploadError(step, msg, _save_debug(page, debug_root, req, step, msg))
    finally:
        if page is not None:
            page.cdp.close()


def _watch(page: Page, timeout: float) -> str:
    """After the hand-over: read the page (never click) until the post shows up. Returns one of WATCH_RESULTS."""
    find = "return {ok: H.hasText(" + json.dumps(SUCCESS_TEXTS) + "), url: location.href};"
    try:
        first = page.try_js(find) or {}
    except OSError:  # ConnectionError: the window or the tab was closed
        return "closed"
    already = bool(first.get("ok"))  # a message already on screen before the hand-over does not count
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if page.cancel.is_set():
            return "stopped"
        try:
            st = page.try_js(find, timeout=10)
        except OSError:
            return "closed"
        if st:
            url = str(st.get("url") or "")
            if (st.get("ok") and not already) or ("/content" in url and "/upload" not in url):
                return "posted"
        page.cancel.wait(1.5)
    return "not_seen"


def _save_debug(page: Optional[Page], root: Path, req: UploadRequest, step: str, message: str) -> Optional[str]:
    try:
        folder = root / time.strftime("%Y%m%d-%H%M%S")
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "log.txt").write_text("\n".join(req.log + [f"FAILED at {step}: {message}"]) + "\n", encoding="utf-8")
        if page is not None:
            page.screenshot_and_html(folder)
        return str(folder)
    except Exception:
        return None


def selftest(out_file: str, mock_html: str, video: str) -> int:
    """CI: the real browser (Edge/Chrome) on the mock upload page. Checks that the form gets filled (file,
    caption, privacy), that the app never presses Post, and that it notices the post once the user makes it
    (the test stands in for the user's click with the mock's __simulateUserPost)."""
    import tempfile
    lines: List[str] = []
    stages: List[str] = []
    ok = False
    try:
        lines.append(f"browser={find_browser()}")
        # the temporary profile is cleaned up by TemporaryDirectory once the browser has been closed
        with tempfile.TemporaryDirectory(prefix="tiktokhq_profile_", ignore_cleanup_errors=True) as prof:
            br = Browser(Path(prof))
            try:
                req = UploadRequest(video=video, caption="تجربة الرفع #TikTokHQ", privacy="only_me",
                                    url=Path(mock_html).resolve().as_uri(), profile=prof, mock=True,
                                    login_timeout=60, upload_timeout=120, watch_timeout=90,
                                    debug_root=str(Path(out_file).resolve().parent / "upload-debug"))
                ok = _drive_mock(br, req, stages, lines)
            finally:
                br.close()
    except Exception as e:  # noqa: BLE001
        lines.append(f"error={type(e).__name__}: {e}")
        if isinstance(e, UploadError) and e.debug_dir:
            lines.append(f"debug={e.debug_dir}")
    lines.insert(0, ("ok" if ok else "FAIL") + " upload-selftest")
    lines.append("stages=" + " > ".join(stages))
    lines.append("done")
    Path(out_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return 0 if ok else 1


def _drive_mock(br: Browser, req: UploadRequest, stages: List[str], lines: List[str]) -> bool:
    """Run prepare_post on the mock page, check the hand-over, play the user's post, check it is noticed."""
    box: Dict[str, Any] = {}

    def work() -> None:
        try:
            box["res"] = prepare_post(req, lambda st, d: stages.append(f"{st}:{d}" if d else st))
        except BaseException as e:  # noqa: BLE001
            box["err"] = e

    th = threading.Thread(target=work, daemon=True)
    th.start()
    deadline = time.monotonic() + req.login_timeout + req.upload_timeout
    while th.is_alive() and "ready" not in stages and time.monotonic() < deadline:
        time.sleep(0.2)
    if "ready" not in stages:
        th.join(5)
        if "err" in box:
            raise box["err"]
        lines.append("hand-over never came")
        return False
    br.port = br._alive_port()
    cdp = br.page(prefer="mock")
    try:
        pg = Page(cdp)
        form = pg.js("return window.__form();") or {}
        lines.append(f"form={json.dumps(form, ensure_ascii=False)}")
        time.sleep(2.0)  # the app must leave the Post button alone
        before = pg.js("return window.__events;") or []
        pg.js("return window.__simulateUserPost();")
        th.join(30)
        events = pg.js("return window.__events;") or []
        lines.append(f"events={json.dumps(events, ensure_ascii=False)}")
    finally:
        cdp.close()
    if "err" in box:
        raise box["err"]
    res = box.get("res") or {}
    lines.append(f"result={json.dumps(res, ensure_ascii=False)}")
    checks = {
        "caption": _norm(form.get("caption", "")) == _norm(req.caption),
        "privacy": form.get("privacy") == "Only you",
        "file": form.get("file") == Path(req.video).name,
        "post_ready": bool(form.get("postEnabled")) and bool(form.get("marked")),
        "post_untouched": "post-click" not in before and "post-click" not in events,
        "noticed": res.get("state") == "posted",
    }
    lines.append("checks=" + " ".join(f"{k}:{'ok' if v else 'FAIL'}" for k, v in checks.items()))
    return all(checks.values())


def main(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="tiktok_hq_upload",
                                 description="Fill TikTok's upload form in your browser; you press Post.")
    ap.add_argument("video")
    ap.add_argument("--caption", default="")
    ap.add_argument("--privacy", choices=sorted(PRIVACY_LABELS), default="only_me")
    ap.add_argument("--url", default=UPLOAD_URL)
    a = ap.parse_args(argv)
    try:
        res = prepare_post(UploadRequest(video=a.video, caption=a.caption, privacy=a.privacy, url=a.url),
                           lambda s, d: print(f"[{s}] {d}" + ("  -> press Post in the browser window" if s == "ready" else ""),
                                              flush=True))
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    except UploadError as e:
        print(f"FAILED at {e.step}: {e.message}" + (f"\n  details saved in {e.debug_dir}" if e.debug_dir else ""))
        return 1
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
