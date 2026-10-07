"""
Tests for tiktok_hq.py.  Run:  python -m unittest discover -s tests -v
Needs ffmpeg + ffprobe on PATH (fixtures are generated with lavfi sources).
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import tiktok_hq as t  # noqa: E402

FFMPEG = shutil.which("ffmpeg")
FFPROBE = shutil.which("ffprobe")


def make_fixture(path: Path, *, size="640x1136", rate="60", dur="2", vcodec=("libx264",), acodec=("aac",),
                 extra=(), audio=True, faststart=True, ar="48000") -> Path:
    cmd = [FFMPEG, "-hide_banner", "-v", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={dur}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}:sample_rate={ar}"]
    cmd += ["-c:v", *vcodec, "-pix_fmt", "yuv420p"]
    if audio:
        cmd += ["-c:a", *acodec, "-shortest"]
    else:
        cmd += ["-an"]
    if faststart:
        cmd += ["-movflags", "+faststart"]
    cmd += [*extra, str(path)]
    subprocess.run(cmd, check=True)
    return path


def framemd5(path: Path, seconds: float, audio: bool = True):
    cmd = [FFMPEG, "-v", "error", "-i", str(path), "-t", str(seconds), "-map", "0:V:0"]
    if audio:
        cmd += ["-map", "0:a:0"]
    cmd += ["-f", "framemd5", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return [l for l in r.stdout.splitlines() if l and not l.startswith("#")], r.stderr


def video_stbl(d: bytes):
    moov = next(b for b in t.iter_boxes(d, 0, len(d)) if b.type == "moov")
    vt = t.find_trak(d, moov, "vide")
    return moov, vt, t.path_box(d, vt, ["mdia", "minf", "stbl"])


def audio_traks(d: bytes):
    moov = next(b for b in t.iter_boxes(d, 0, len(d)) if b.type == "moov")
    return moov, [tr for tr in t.children(d, moov) if tr.type == "trak" and t.handler_of(d, tr) == "soun"]


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not available")
class AudioGhostTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="tiktokhq_test_"))
        cls.src = make_fixture(cls.tmp / "src.mp4", extra=("-preset", "veryfast", "-crf", "24", "-bf", "2"))
        cls.src_bytes = cls.src.read_bytes()
        cls.out, cls.stats = t.audio_ghost_patch(cls.src_bytes, multiplier=10)
        cls.out_path = cls.tmp / "audio.mp4"
        cls.out_path.write_bytes(cls.out)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_clone_track_tables(self):
        _, auds_src = audio_traks(self.src_bytes)
        moov, auds = audio_traks(self.out)
        self.assertEqual(len(auds_src), 1)
        self.assertEqual(len(auds), 2)
        real, clone = auds
        s_real = t.path_box(self.out, real, ["mdia", "minf", "stbl"])
        s_clone = t.path_box(self.out, clone, ["mdia", "minf", "stbl"])
        n_real = len(t.parse_stsz(self.out, t.child(self.out, s_real, "stsz")))
        sizes = t.parse_stsz(self.out, t.child(self.out, s_clone, "stsz"))
        self.assertEqual(len(sizes), n_real * 10)
        self.assertEqual(self.stats["audio_ghost_samples"], n_real * 9)
        self.assertTrue(all(s == 8 for s in sizes[n_real:]))
        stts = t.parse_stts(self.out, t.child(self.out, s_clone, "stts"))
        self.assertEqual(stts[-1], (n_real * 9, 1))
        self.assertEqual(sum(c for c, _ in stts), n_real * 10)
        stsc = t.parse_stsc(self.out, t.child(self.out, s_clone, "stsc"))
        co = t.parse_chunk_offsets(self.out, t.child(self.out, s_clone, "stco") or t.child(self.out, s_clone, "co64"))
        self.assertEqual(stsc[-1][1], n_real * 9)
        self.assertEqual(stsc[-1][0], len(co))
        self.assertEqual(self.out[co[-1]:co[-1] + 8], t.GHOST_SAMPLE)
        self.assertEqual(self.out[co[-1]:co[-1] + 8 * n_real * 9], t.GHOST_SAMPLE * (n_real * 9))
        # ghost data lives in a trailing mdat box
        top = list(t.iter_boxes(self.out, 0, len(self.out)))
        self.assertEqual([b.type for b in top][-2:], ["mdat", "mdat"])
        self.assertEqual(top[-1].body, co[-1])
        # clone has no edts and a new track id, mvhd next_track_id bumped
        self.assertIsNone(t.child(self.out, clone, "edts"))
        tk_real = t.child(self.out, real, "tkhd")
        tk_clone = t.child(self.out, clone, "tkhd")
        self.assertNotEqual(t.u32(self.out, tk_real.body + 12), t.u32(self.out, tk_clone.body + 12))
        mvhd = t.child(self.out, moov, "mvhd")
        self.assertEqual(t.u32(self.out, mvhd.end - 4), t.u32(self.out, tk_clone.body + 12) + 1)

    def test_real_tracks_untouched(self):
        for handler in ("vide", "soun"):
            moov_s = next(b for b in t.iter_boxes(self.src_bytes, 0, len(self.src_bytes)) if b.type == "moov")
            moov_o = next(b for b in t.iter_boxes(self.out, 0, len(self.out)) if b.type == "moov")
            st_s = t.path_box(self.src_bytes, t.find_trak(self.src_bytes, moov_s, handler), ["mdia", "minf", "stbl"])
            st_o = t.path_box(self.out, t.find_trak(self.out, moov_o, handler), ["mdia", "minf", "stbl"])
            sz_s = t.parse_stsz(self.src_bytes, t.child(self.src_bytes, st_s, "stsz"))
            sz_o = t.parse_stsz(self.out, t.child(self.out, st_o, "stsz"))
            self.assertEqual(len(sz_s), len(sz_o))
            co_s = t.parse_chunk_offsets(self.src_bytes, t.child(self.src_bytes, st_s, "stco"))
            co_o = t.parse_chunk_offsets(self.out, t.child(self.out, st_o, "stco") or t.child(self.out, st_o, "co64"))
            self.assertEqual(len(co_s), len(co_o))
            # every chunk after the first points at identical bytes; the first video chunk lost its SEI
            for i, (a, b) in enumerate(zip(co_s, co_o)):
                if handler == "vide" and i == 0:
                    continue
                self.assertEqual(self.src_bytes[a:a + 48], self.out[b:b + 48], f"{handler} chunk {i}")

    def test_signatures_cleaned(self):
        self.assertGreater(self.stats["sei_removed"], 0)
        self.assertNotIn(t.X264_SEI_UUID, self.out)
        self.assertNotIn(b"x264 - core", self.out)
        self.assertGreater(self.stats["btrt_removed"], 0)
        self.assertNotIn(b"btrt", self.out)
        self.assertNotIn(b"Lavf", self.out)
        self.assertIn(b"te_is_reencode", self.out)
        self.assertIn(t.REPLICA_COMPRESSORNAME.encode(), self.out)
        out_plain, st = t.audio_ghost_patch(self.src_bytes, multiplier=3, tags="plain")
        self.assertIn(b"Lavf59.27.100", out_plain)
        self.assertNotIn(t.REPLICA_COMPRESSORNAME.encode(), out_plain)

    def test_decodes_identically(self):
        ref, _ = framemd5(self.src, 1.9)
        got, err = framemd5(self.out_path, 1.9)
        self.assertEqual(ref, got)
        self.assertEqual([l for l in err.splitlines() if l.strip()], [])
        an = t.probe(FFPROBE, self.out_path)
        self.assertEqual(an.audio_streams, 2)
        self.assertEqual(an.vcodec, "h264")
        self.assertAlmostEqual(an.fps, 60.0, places=2)
        self.assertEqual(an.audio_nb_frames[1], an.audio_nb_frames[0] * 10)

    def test_no_audio_rejected(self):
        silent = make_fixture(self.tmp / "silent.mp4", audio=False, dur="1", extra=("-preset", "veryfast"))
        with self.assertRaises(t.MP4Error):
            t.audio_ghost_patch(silent.read_bytes())

    def test_rejects_mdat_first(self):
        slow = make_fixture(self.tmp / "mdatfirst.mp4", faststart=False, dur="1", extra=("-preset", "veryfast"))
        with self.assertRaises(t.MP4Error):
            t.audio_ghost_patch(slow.read_bytes())


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not available")
class GhostVideoTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="tiktokhq_test_"))
        cls.src = make_fixture(cls.tmp / "src.mp4", extra=("-preset", "veryfast", "-crf", "24", "-bf", "2"))
        cls.src_bytes = cls.src.read_bytes()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_ghost_tables(self):
        out, stats = t.ghost_patch(self.src_bytes, multiplier=10)
        self.assertEqual(stats["declared_frames"], stats["real_frames"] * 10)
        _, _, stbl = video_stbl(out)
        sizes = t.parse_stsz(out, t.child(out, stbl, "stsz"))
        stts = t.parse_stts(out, t.child(out, stbl, "stts"))
        stsc = t.parse_stsc(out, t.child(out, stbl, "stsc"))
        co = t.parse_chunk_offsets(out, t.child(out, stbl, "stco") or t.child(out, stbl, "co64"))
        real, ghost = stats["real_frames"], stats["ghost_frames"]
        self.assertEqual(len(sizes), real + ghost)
        self.assertTrue(all(s == 8 for s in sizes[real:]))
        self.assertEqual(sum(c for c, _ in stts), real + ghost)
        self.assertEqual(stsc[-1][1], 1)
        self.assertEqual(len(co), stsc[-1][0] - 1 + ghost)
        self.assertTrue(all(o == co[-1] for o in co[-ghost:]))
        self.assertEqual(out[co[-1]:co[-1] + 8], t.GHOST_SAMPLE)
        self.assertEqual(co[-1] + 8, len(out))
        ctts = t.child(out, stbl, "ctts")
        if ctts:
            _, entries = t.parse_ctts(out, ctts)
            self.assertEqual(sum(c for c, _ in entries), real + ghost)

    def test_media_bytes_unchanged(self):
        out, _ = t.ghost_patch(self.src_bytes, multiplier=5)
        self.assertEqual(t.media_sha256(self.src_bytes), t.media_sha256(out, strip_ghost_tail=True))

    def test_ffprobe_and_decode(self):
        out, stats = t.ghost_patch(self.src_bytes, multiplier=10)
        p = self.tmp / "ghost.mp4"
        p.write_bytes(out)
        an = t.probe(FFPROBE, p)
        self.assertEqual(an.nb_frames, stats["declared_frames"])
        ref, _ = framemd5(self.src, 1.5)
        got, err = framemd5(p, 1.5)
        self.assertEqual(ref, got)
        self.assertEqual([l for l in err.splitlines() if l.strip() and not t.is_ghost_noise(l)], [])

    def test_rejects_non_h264(self):
        hevc = make_fixture(self.tmp / "hevc.mp4", vcodec=("libx265", "-tag:v", "hvc1"),
                            extra=("-preset", "ultrafast", "-x265-params", "log-level=error"), dur="1")
        with self.assertRaises(t.MP4Error):
            t.ghost_patch(hevc.read_bytes())


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not available")
class OtherPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="tiktokhq_test_"))
        cls.src = make_fixture(cls.tmp / "src.mp4", extra=("-preset", "veryfast", "-crf", "24"))
        cls.src_bytes = cls.src.read_bytes()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_elst_patch(self):
        out, stats = t.elst_patch(self.src_bytes)
        _, vt, _ = video_stbl(out)
        elst = t.path_box(out, vt, ["edts", "elst"])
        self.assertIsNotNone(elst)
        self.assertEqual(out[elst.body + 4:elst.body + 8], b"\x10\x00\x00\x01")   # entry_count field
        self.assertEqual(t.media_sha256(self.src_bytes), t.media_sha256(out))
        p = self.tmp / "elst.mp4"
        p.write_bytes(out)
        self.assertEqual(t.probe(FFPROBE, p).vcodec, "h264")

    def test_fps_patch(self):
        out, stats = t.fps_patch(self.src_bytes, 2)
        self.assertEqual(len(out), len(self.src_bytes))
        self.assertEqual(t.media_sha256(self.src_bytes), t.media_sha256(out))
        p = self.tmp / "fps.mp4"
        p.write_bytes(out)
        self.assertAlmostEqual(t.probe(FFPROBE, p).fps, 30.0, places=1)

    def test_decide(self):
        an = t.probe(FFPROBE, self.src)
        dec = t.decide(an)
        self.assertEqual(dec.video, "copy")
        self.assertEqual(dec.audio, "copy")
        a441 = make_fixture(self.tmp / "a441.mp4", dur="1", ar="44100", extra=("-preset", "veryfast"))
        self.assertEqual(t.decide(t.probe(FFPROBE, a441)).audio, "encode")
        hevc = make_fixture(self.tmp / "hevc.mp4", vcodec=("libx265", "-tag:v", "hvc1"),
                            extra=("-preset", "ultrafast", "-x265-params", "log-level=error"), dur="1")
        self.assertEqual(t.decide(t.probe(FFPROBE, hevc)).video, "encode")
        with self.assertRaises(t.ToolError):
            t.decide(t.probe(FFPROBE, hevc), no_encode=True)
        silent = make_fixture(self.tmp / "silent.mp4", audio=False, dur="1", extra=("-preset", "veryfast"))
        self.assertEqual(t.decide(t.probe(FFPROBE, silent)).audio, "silent")


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not available")
class CliTests(unittest.TestCase):
    def test_prep_check_compare_end_to_end(self):
        tmp = Path(tempfile.mkdtemp(prefix="tiktokhq_cli_"))
        try:
            src = make_fixture(tmp / "clip.mp4", extra=("-preset", "veryfast", "-crf", "24"))
            self.assertEqual(t.main([str(src), "--out-dir", str(tmp / "out")]), 0)
            out = tmp / "out" / "clip_TikTokHQ.mp4"
            self.assertTrue(out.is_file())
            self.assertEqual(t.probe(FFPROBE, out).audio_streams, 2)
            self.assertEqual(t.main(["check", str(out)]), 0)
            self.assertEqual(t.main(["compare", str(out), str(out)]), 0)
            self.assertEqual(t.main(["compare", str(src), str(out)]), 0)   # same picture, re-muxed
            # silent source -> silent track added, audio method still works
            silent = make_fixture(tmp / "silent.mp4", audio=False, dur="1", extra=("-preset", "veryfast"))
            self.assertEqual(t.main([str(silent), "--out-dir", str(tmp / "out")]), 0)
            self.assertEqual(t.probe(FFPROBE, tmp / "out" / "silent_TikTokHQ.mp4").audio_streams, 2)
            # all methods
            self.assertEqual(t.main([str(src), "--method", "all", "--out-dir", str(tmp / "all")]), 0)
            for m in t.METHODS:
                self.assertTrue((tmp / "all" / f"clip_TikTokHQ_{m}.mp4").is_file(), m)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class DesktopHooksTests(unittest.TestCase):
    """Hooks used by the desktop window / packaged app."""

    def tearDown(self):
        t.set_output(t._print_line, t._print_progress)
        for attr in ("frozen", "_MEIPASS"):
            if hasattr(sys, attr):
                delattr(sys, attr)

    def test_set_output_routes_say(self):
        got = []
        t.set_output(got.append)
        t.say("hello")
        t.say("")
        self.assertEqual(got, ["hello", ""])

    def test_find_tool_in_frozen_bundle(self):
        with tempfile.TemporaryDirectory() as d:
            exe = Path(d) / ("ffmpeg.exe" if t.os.name == "nt" else "ffmpeg")
            exe.write_bytes(b"#!/bin/sh\n")
            sys.frozen = True
            sys._MEIPASS = d
            self.assertEqual(t.find_tool("ffmpeg"), str(exe))

    def test_gui_smoke_mode_needs_no_window(self):
        with tempfile.TemporaryDirectory() as d:
            out = Path(d) / "smoke.txt"
            r = subprocess.run([sys.executable, str(HERE.parent / "tiktok_hq_gui.py"), "--smoke", str(out)],
                               capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            text = out.read_text(encoding="utf-8")
            self.assertTrue(text.startswith("ok "), text)
            self.assertIn("ffmpeg version", text)


if __name__ == "__main__":
    unittest.main()
