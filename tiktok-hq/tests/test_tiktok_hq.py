"""
Tests for tiktok_hq.py.  Run:  python -m unittest discover -s tests -v
Needs ffmpeg + ffprobe on PATH (fixtures are generated with lavfi sources).
"""
from __future__ import annotations

import os
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
                 extra=(), audio=True, faststart=True) -> Path:
    cmd = [FFMPEG, "-hide_banner", "-v", "error", "-y",
           "-f", "lavfi", "-i", f"testsrc2=size={size}:rate={rate}:duration={dur}"]
    if audio:
        cmd += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={dur}"]
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


def video_stbl(d: bytes):
    moov = next(b for b in t.iter_boxes(d, 0, len(d)) if b.type == "moov")
    vt = t.find_trak(d, moov, "vide")
    return moov, vt, t.path_box(d, vt, ["mdia", "minf", "stbl"])


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not available")
class GhostPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="tiktokhq_test_"))
        cls.src = make_fixture(cls.tmp / "src.mp4", extra=("-preset", "ultrafast", "-crf", "24", "-bf", "2"))
        cls.src_bytes = cls.src.read_bytes()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_box_parser_roundtrip(self):
        top = [b.type for b in t.iter_boxes(self.src_bytes, 0, len(self.src_bytes))]
        self.assertIn("ftyp", top)
        self.assertIn("moov", top)
        self.assertIn("mdat", top)
        self.assertLess(top.index("moov"), top.index("mdat"))

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
        self.assertEqual(stsc[-1][1], 1)                      # one ghost sample per ghost chunk
        self.assertEqual(len(co), stsc[-1][0] - 1 + ghost)    # chunk count matches stsc
        self.assertTrue(all(o == co[-1] for o in co[-ghost:]))
        self.assertEqual(out[co[-1]:co[-1] + 8], t.GHOST_SAMPLE)
        self.assertEqual(co[-1] + 8, len(out))                # filler is the last 8 bytes of mdat
        ctts = t.child(out, stbl, "ctts")
        if ctts:
            _, entries = t.parse_ctts(out, ctts)
            self.assertEqual(sum(c for c, _ in entries), real + ghost)

    def test_media_bytes_unchanged(self):
        out, _ = t.ghost_patch(self.src_bytes, multiplier=5)
        self.assertEqual(t.media_sha256(self.src_bytes), t.media_sha256(out, strip_ghost_tail=True))
        self.assertNotEqual(t.media_sha256(self.src_bytes), t.media_sha256(out))

    def test_real_chunks_point_at_same_bytes(self):
        out, stats = t.ghost_patch(self.src_bytes, multiplier=3)
        _, _, s_stbl = video_stbl(self.src_bytes)
        _, _, o_stbl = video_stbl(out)
        s_co = t.parse_chunk_offsets(self.src_bytes, t.child(self.src_bytes, s_stbl, "stco"))
        o_co = t.parse_chunk_offsets(out, t.child(out, o_stbl, "stco") or t.child(out, o_stbl, "co64"))
        sizes = t.parse_stsz(self.src_bytes, t.child(self.src_bytes, s_stbl, "stsz"))
        for so, oo in zip(s_co, o_co[:len(s_co)]):
            self.assertEqual(self.src_bytes[so:so + 64], out[oo:oo + 64])
        self.assertEqual(self.src_bytes[s_co[0]:s_co[0] + sizes[0]], out[o_co[0]:o_co[0] + sizes[0]])
        # audio chunks too
        moov_s = next(b for b in t.iter_boxes(self.src_bytes, 0, len(self.src_bytes)) if b.type == "moov")
        moov_o = next(b for b in t.iter_boxes(out, 0, len(out)) if b.type == "moov")
        a_s = t.path_box(self.src_bytes, t.find_trak(self.src_bytes, moov_s, "soun"), ["mdia", "minf", "stbl"])
        a_o = t.path_box(out, t.find_trak(out, moov_o, "soun"), ["mdia", "minf", "stbl"])
        as_co = t.parse_chunk_offsets(self.src_bytes, t.child(self.src_bytes, a_s, "stco"))
        ao_co = t.parse_chunk_offsets(out, t.child(out, a_o, "stco"))
        self.assertEqual(len(as_co), len(ao_co))
        for so, oo in zip(as_co, ao_co):
            self.assertEqual(self.src_bytes[so:so + 32], out[oo:oo + 32])

    def test_ffprobe_and_decode(self):
        out, stats = t.ghost_patch(self.src_bytes, multiplier=10)
        p = self.tmp / "ghost.mp4"
        p.write_bytes(out)
        an = t.probe(FFPROBE, p)
        self.assertEqual(an.vcodec, "h264")
        self.assertEqual(an.nb_frames, stats["declared_frames"])
        self.assertAlmostEqual(an.fps, 60.0, places=2)
        r = subprocess.run([FFMPEG, "-v", "error", "-i", str(p), "-t", "1.5", "-map", "0:V:0", "-f", "null", "-"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        real_errors = [l for l in r.stderr.splitlines() if l.strip() and not t.is_ghost_noise(l)]
        self.assertEqual(real_errors, [])

    def test_replica_strips_sei(self):
        out, stats = t.ghost_patch(self.src_bytes, multiplier=2, replica=True)
        self.assertGreater(stats["sei_bytes_removed"], 0)
        p = self.tmp / "replica.mp4"
        p.write_bytes(out)
        r = subprocess.run([FFMPEG, "-v", "error", "-i", str(p), "-t", "1.0", "-map", "0:V:0", "-f", "null", "-"],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn(b"SoundHandler", out)
        self.assertIn(b"TikTokHQ", out)

    def test_rejects_non_h264(self):
        hevc = make_fixture(self.tmp / "hevc.mp4", vcodec=("libx265", "-tag:v", "hvc1"), extra=("-preset", "ultrafast", "-x265-params", "log-level=error"), dur="1")
        with self.assertRaises(t.MP4Error):
            t.ghost_patch(hevc.read_bytes())

    def test_rejects_mdat_first(self):
        slow = make_fixture(self.tmp / "mdatfirst.mp4", faststart=False, dur="1", extra=("-preset", "ultrafast"))
        with self.assertRaises(t.MP4Error):
            t.ghost_patch(slow.read_bytes())


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not available")
class OtherPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="tiktokhq_test_"))
        cls.src = make_fixture(cls.tmp / "src.mp4", extra=("-preset", "ultrafast", "-crf", "24"))
        cls.src_bytes = cls.src.read_bytes()

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp, ignore_errors=True)

    def test_elst_patch(self):
        out, stats = t.elst_patch(self.src_bytes)
        _, vt, _ = video_stbl(out)
        elst = t.path_box(out, vt, ["edts", "elst"])
        self.assertIsNotNone(elst)
        self.assertEqual(out[elst.body:elst.body + 4], b"\x10\x00\x00\x01")
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
        an = t.probe(FFPROBE, p)
        self.assertAlmostEqual(an.fps, 30.0, places=1)

    def test_decide(self):
        an = t.probe(FFPROBE, self.src)
        dec = t.decide(an)
        self.assertEqual(dec.video, "copy")
        self.assertEqual(dec.audio, "copy")
        hevc = make_fixture(self.tmp / "hevc.mp4", vcodec=("libx265", "-tag:v", "hvc1"), extra=("-preset", "ultrafast", "-x265-params", "log-level=error"), dur="1")
        dec2 = t.decide(t.probe(FFPROBE, hevc))
        self.assertEqual(dec2.video, "encode")
        with self.assertRaises(t.ToolError):
            t.decide(t.probe(FFPROBE, hevc), no_encode=True)


@unittest.skipUnless(FFMPEG and FFPROBE, "ffmpeg/ffprobe not available")
class CliTests(unittest.TestCase):
    def test_prep_and_compare_end_to_end(self):
        tmp = Path(tempfile.mkdtemp(prefix="tiktokhq_cli_"))
        try:
            src = make_fixture(tmp / "clip.mp4", extra=("-preset", "ultrafast", "-crf", "24"))
            rc = t.main([str(src), "--out-dir", str(tmp / "out")])
            self.assertEqual(rc, 0)
            out = tmp / "out" / "clip_TikTokHQ.mp4"
            self.assertTrue(out.is_file())
            self.assertEqual(t.main(["check", str(out)]), 0)
            self.assertEqual(t.main(["compare", str(out), str(out)]), 0)
            self.assertEqual(t.main(["compare", str(src), str(out)]), 0)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
