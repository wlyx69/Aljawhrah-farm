#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Tests for tiktok_hq_upload (browser hand-over to TikTok's upload page).

The browser tests drive a real Edge/Chrome/Chromium on tests/fixtures/mock_studio.html and are skipped
when no browser is found. TIKTOKHQ_BROWSER picks the browser, TIKTOKHQ_BROWSER_ARGS adds switches
(CI and containers: "--headless=new", plus "--no-sandbox" when running as root).
"""
from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import tiktok_hq_upload as up  # noqa: E402
import tiktok_hq_ui  # noqa: E402

MOCK = HERE / "fixtures" / "mock_studio.html"


class FrameTests(unittest.TestCase):
    def test_client_frames_roundtrip(self):
        for n in (0, 1, 125, 126, 65535, 65536, 70000):
            text = "ع" * (n // 2) + "x" * (n % 2)
            fr = up.encode_frame(text, key=b"\x01\x02\x03\x04")
            self.assertTrue(fr[1] & 0x80, "client frames are masked")
            fin, opcode, payload, used = up.parse_frame(fr + b"tail")
            self.assertTrue(fin)
            self.assertEqual(opcode, 0x1)
            self.assertEqual(payload.decode("utf-8"), text)
            self.assertEqual(used, len(fr))

    def test_incomplete_frames(self):
        fr = up.encode_frame("x" * 300)
        for cut in (0, 1, 3, 7, len(fr) - 1):
            self.assertIsNone(up.parse_frame(fr[:cut]))

    def test_server_frame_unmasked(self):
        self.assertEqual(up.parse_frame(b"\x81\x05hello"), (True, 0x1, b"hello", 7))

    def test_recv_answers_ping_and_joins_fragments(self):
        a, b = socket.socketpair()
        try:
            ws = up.WebSocket.__new__(up.WebSocket)
            ws.sock, ws.buf, ws.lock = a, b"", threading.Lock()
            # ping, then a text message in two fragments (fin=0 text, fin=1 continuation)
            b.sendall(b"\x89\x02hi" + b"\x01\x03abc" + b"\x80\x03def")
            self.assertEqual(ws.recv(5), "abcdef")
            b.settimeout(5)
            pong = up.parse_frame(b.recv(64))
            self.assertEqual((pong[1], pong[2]), (0xA, b"hi"))
            with self.assertRaises(TimeoutError):
                ws.recv(0.2)
            b.sendall(b"\x88\x00")  # close frame
            with self.assertRaises(ConnectionError):
                ws.recv(5)
        finally:
            a.close()
            b.close()


class HelperTests(unittest.TestCase):
    def test_norm(self):
        self.assertEqual(up._norm("  تجربة​  الرفع\n#TikTokHQ ﻿"), "تجربة الرفع #TikTokHQ")
        self.assertEqual(up._norm(None), "")

    def test_env_browser_comes_first(self):
        old = os.environ.get("TIKTOKHQ_BROWSER")
        os.environ["TIKTOKHQ_BROWSER"] = "/opt/some/browser"
        try:
            self.assertEqual(up.browser_candidates()[0], Path("/opt/some/browser"))
        finally:
            if old is None:
                os.environ.pop("TIKTOKHQ_BROWSER", None)
            else:
                os.environ["TIKTOKHQ_BROWSER"] = old

    def test_chrome_comes_before_edge(self):
        self.assertTrue(up.WINDOWS_BROWSERS[0].endswith("chrome.exe"))
        self.assertTrue(up.WINDOWS_BROWSERS[1].endswith("msedge.exe"))
        self.assertEqual(up.MAC_BROWSERS[0], "Google Chrome")
        self.assertEqual(up.LINUX_BROWSERS[0], "google-chrome")

    def test_each_browser_has_its_own_profile(self):
        kinds = {name: up.browser_kind(Path(name)) for name in
                 ("chrome.exe", "msedge.exe", "brave.exe", "Google Chrome", "Microsoft Edge", "chromium-browser")}
        self.assertEqual(kinds, {"chrome.exe": "chrome", "msedge.exe": "edge", "brave.exe": "brave",
                                 "Google Chrome": "chrome", "Microsoft Edge": "edge", "chromium-browser": "chromium"})
        self.assertNotEqual(up.profile_dir(Path("chrome.exe")), up.profile_dir(Path("msedge.exe")))
        self.assertEqual(up.profile_dir(Path("Google Chrome")).name, "browser-chrome")

    def test_bad_input_fails_before_any_browser(self):
        with self.assertRaises(up.UploadError) as cm:
            up.prepare_post(up.UploadRequest(video="/no/such/file.mp4"), lambda s, d: None)
        self.assertEqual(cm.exception.step, "attach")
        with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
            with self.assertRaises(up.UploadError) as cm:
                up.prepare_post(up.UploadRequest(video=f.name, privacy="public!"), lambda s, d: None)
            self.assertEqual(cm.exception.step, "details")

    def test_no_code_path_presses_post(self):
        src = (HERE.parent / "tiktok_hq_upload.py").read_text(encoding="utf-8")
        self.assertNotIn("POST_NOW", src)
        self.assertNotIn("click(POST_BUTTON_JS", src)
        for t in up.DISMISS_TEXTS:  # pop-up buttons the app may close: none of them posts
            self.assertNotIn("post", t.lower())
            self.assertNotIn("نشر", t)


class WindowApiTests(unittest.TestCase):
    """tiktok_hq_ui.Api.upload input checks (no browser needed)."""

    def test_refuses_bad_input(self):
        api = tiktok_hq_ui.Api()
        self.assertEqual(api.upload("/no/such/file.mp4", {})["started"], False)
        with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
            r = api.upload(f.name, {"privacy": "public!"})
            self.assertFalse(r["started"])
            self.assertIn("privacy", r["error"])
        self.assertFalse(api.cancel_upload())
        self.assertEqual(api.poll(), [])

    def test_one_upload_at_a_time(self):
        api = tiktok_hq_ui.Api()
        api._uploading = True
        with tempfile.NamedTemporaryFile(suffix=".mp4") as f:
            self.assertEqual(api.upload(f.name, {}), {"started": False, "error": "upload_busy"})


@unittest.skipUnless(up.find_browser(), "no Edge/Chrome/Chromium found (set TIKTOKHQ_BROWSER)")
class MockBrowserTests(unittest.TestCase):
    """The whole flow on the mock upload page in a real browser."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="tiktokhq_test_", ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name)
        self.video = self.dir / "clip test.mp4"
        self.video.write_bytes(os.urandom(200_000))
        self.profile = self.dir / "profile"
        self.browser = up.Browser(self.profile)

    def tearDown(self):
        self.browser.close()
        self.tmp.cleanup()

    def request(self, query: str = "") -> up.UploadRequest:
        return up.UploadRequest(video=str(self.video), caption="وصف تجريبي #تجربة @TikTokHQ", privacy="only_me",
                                url=MOCK.resolve().as_uri() + query, profile=str(self.profile), mock=True,
                                login_timeout=60, upload_timeout=120, watch_timeout=60,
                                debug_root=str(self.dir / "debug"))

    def start(self, req: up.UploadRequest, cancel: threading.Event = None):
        stages, box = [], {}

        def work():
            try:
                box["res"] = up.prepare_post(req, lambda s, d: stages.append(s), cancel)
            except BaseException as e:  # noqa: BLE001
                box["err"] = e

        th = threading.Thread(target=work, daemon=True)
        th.start()
        return th, stages, box

    def wait_ready(self, th, stages):
        deadline = time.monotonic() + 150
        while th.is_alive() and "ready" not in stages and time.monotonic() < deadline:
            time.sleep(0.2)
        self.assertIn("ready", stages)

    def mock_events(self):
        self.browser.port = self.browser._alive_port()
        cdp = self.browser.page(prefer="mock")
        try:
            return up.Page(cdp).js("return window.__events;") or []
        finally:
            cdp.close()

    def test_selftest_fills_form_hands_over_and_notices_post(self):
        out = self.dir / "selftest.txt"
        rc = up.selftest(str(out), str(MOCK), str(self.video))
        text = out.read_text(encoding="utf-8")
        self.assertEqual(rc, 0, text)
        self.assertTrue(text.startswith("ok upload-selftest"), text)
        self.assertIn("post_untouched:ok", text)
        self.assertIn("> ready > posted", text)

    def test_privacy_that_cannot_be_set_stops_before_hand_over(self):
        th, stages, box = self.start(self.request("?noprivate=1"))
        th.join(180)
        err = box.get("err")
        self.assertIsInstance(err, up.UploadError, box)
        self.assertEqual((err.step, err.message), ("details", "privacy_not_confirmed"))
        self.assertNotIn("ready", stages)
        self.assertTrue(err.debug_dir and (Path(err.debug_dir) / "log.txt").is_file())
        self.assertNotIn("post-click", self.mock_events())

    def test_stop_watching(self):
        cancel = threading.Event()
        th, stages, box = self.start(self.request(), cancel)
        self.wait_ready(th, stages)
        cancel.set()
        th.join(15)
        self.assertEqual(box.get("res", {}).get("state"), "stopped", box)
        self.assertNotIn("post-click", self.mock_events())

    def test_window_api_events(self):
        api = tiktok_hq_ui.Api()
        req = self.request()
        api.upload_overrides = {k: getattr(req, k) for k in ("url", "profile", "mock", "login_timeout",
                                                            "upload_timeout", "watch_timeout", "debug_root")}
        r = api.upload(str(self.video), {"privacy": "only_me", "caption": req.caption})
        self.assertEqual(r, {"started": True})
        self.assertEqual(api.upload(str(self.video), {})["error"], "upload_busy")
        events = []

        def drain(kind, stage=None, timeout=150):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                batch = api.poll()
                events.extend(batch)
                for e in batch:
                    if e["kind"] == kind and (stage is None or e.get("stage") == stage):
                        return e
                time.sleep(0.1)
            self.fail(f"no {kind}/{stage}: {[(e['kind'], e.get('stage')) for e in events]}")

        drain("upload_stage", "ready")
        self.browser.port = self.browser._alive_port()
        cdp = self.browser.page(prefer="mock")
        try:
            up.Page(cdp).js("return window.__simulateUserPost();")
        finally:
            cdp.close()
        done = drain("upload_done")
        self.assertEqual(done["state"], "posted")
        kinds = [e.get("stage") for e in events if e["kind"] == "upload_stage"]
        self.assertEqual([k for k in ("browser", "login", "attach", "uploading", "details", "ready") if k in kinds],
                         ["browser", "login", "attach", "uploading", "details", "ready"])
        self.assertTrue(any(e["kind"] == "line" and e["text"].startswith("[upload] ready") for e in events))
        self.assertFalse(api._uploading)
        self.assertNotIn("post-click", self.mock_events())

    def test_open_window_gets_a_new_tab_with_the_whole_address(self):
        # a window left open by an earlier upload: the old tab stays as it is, the form opens in a new tab, and
        # the address keeps everything after "&" (here the switch that makes the mock refuse "Only you")
        self.browser.start(MOCK.resolve().as_uri())
        th, stages, box = self.start(self.request("?a=1&noprivate=1"))
        th.join(180)
        self.assertEqual(getattr(box.get("err"), "message", None), "privacy_not_confirmed", box)
        urls = sorted(t["url"] for t in self.browser._targets())
        self.assertEqual(len(urls), 2, urls)
        self.assertTrue(urls[0].endswith("mock_studio.html"), urls)
        self.assertTrue(urls[1].endswith("mock_studio.html?a=1&noprivate=1"), urls)

    def test_window_closed_while_watching(self):
        th, stages, box = self.start(self.request())
        self.wait_ready(th, stages)
        self.browser.port = self.browser._alive_port()
        self.browser.close()
        th.join(20)
        self.assertEqual(box.get("res", {}).get("state"), "closed", box)


if __name__ == "__main__":
    unittest.main()
