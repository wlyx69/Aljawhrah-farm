#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TikTok HQ - prepare a video so TikTok serves it as-is ("Original" quality).

What it does (default "prep" pipeline):
  1. probe   : read the source with ffprobe
  2. encode  : ONLY if needed (not H.264 / 10-bit / HDR / >1080p / >60 fps /
               non-AAC audio): one high-quality libx264 pass.
               Otherwise a lossless remux (-c copy): the picture is untouched.
  3. patch   : container-only "ghost frames" patch (no re-encode). This is the
               technique used by the community "TikTok Enhancer" family of tools.
  4. verify  : ffprobe + decode check + proof that the media bytes are unchanged

Single file. No third-party Python packages. Needs ffmpeg + ffprobe.

Usage:
  python tiktok_hq.py VIDEO                      -> VIDEO_TikTokHQ.mp4
  python tiktok_hq.py VIDEO --method ghost|elst|fps|all
  python tiktok_hq.py check VIDEO                -> analysis only
  python tiktok_hq.py compare SERVED.mp4 UPLOADED.mp4
  python tiktok_hq.py                            -> file picker (double-click)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple

VERSION = "1.0.0"
OUTPUT_SUFFIX = "_TikTokHQ"

# The 8-byte filler sample every ghost frame points to:
#   00 00 00 04  -> NAL unit length = 4
#   00 00 00 00  -> NAL header type 0 ("unspecified"), decoders ignore it
GHOST_SAMPLE = b"\x00\x00\x00\x04\x00\x00\x00\x00"

# ffprobe reports these transfer characteristics for HDR sources
HDR_TRANSFERS = {"smpte2084", "arib-std-b67", "smpte428", "bt2020-10", "bt2020-12"}

# Official TikTok Content Posting API limits (developers.tiktok.com media transfer guide)
TIKTOK_MAX_FILE_BYTES = 4 * 1024 * 1024 * 1024
TIKTOK_MIN_FPS = 23
TIKTOK_MAX_FPS = 60
TIKTOK_MIN_SIDE = 360
TIKTOK_MAX_SIDE = 4096


# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #

class ToolError(Exception):
    """A user-facing error (printed without a traceback)."""


class MP4Error(ToolError):
    """The MP4 structure could not be parsed or patched safely."""


def say(msg: str = "") -> None:
    print(msg, flush=True)


def human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024.0
    return f"{n:.1f} GB"


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_tool(name: str) -> Optional[str]:
    """Find ffmpeg/ffprobe on PATH, next to this script, or in ./bin."""
    here = Path(__file__).resolve().parent
    exe = name + (".exe" if os.name == "nt" else "")
    for candidate in (here / exe, here / "bin" / exe, here / "ffmpeg" / exe, here / "ffmpeg" / "bin" / exe):
        if candidate.is_file():
            return str(candidate)
    return shutil.which(name)


def require_tools() -> Tuple[str, str]:
    ffmpeg = find_tool("ffmpeg")
    ffprobe = find_tool("ffprobe")
    if not ffmpeg or not ffprobe:
        raise ToolError(
            "ffmpeg/ffprobe not found.\n"
            "  Windows : winget install Gyan.FFmpeg   (then open a NEW terminal)\n"
            "  macOS   : brew install ffmpeg\n"
            "  Linux   : sudo apt install ffmpeg\n"
            "  ...or put ffmpeg.exe and ffprobe.exe next to tiktok_hq.py"
        )
    return ffmpeg, ffprobe


def run(cmd: List[str], timeout: Optional[int] = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=timeout)
    except FileNotFoundError as e:
        raise ToolError(f"cannot run {cmd[0]}: {e}")


# --------------------------------------------------------------------------- #
# Probe + decision
# --------------------------------------------------------------------------- #

@dataclass
class Analysis:
    path: Path
    size: int = 0
    container: str = ""
    duration: float = 0.0
    bitrate: int = 0
    has_video: bool = False
    vcodec: str = ""
    vprofile: str = ""
    vlevel: int = 0
    width: int = 0
    height: int = 0
    rotation: int = 0
    fps: float = 0.0
    avg_fps: float = 0.0
    nb_frames: int = 0
    pix_fmt: str = ""
    color_transfer: str = ""
    color_primaries: str = ""
    color_space: str = ""
    field_order: str = ""
    vbitrate: int = 0
    has_audio: bool = False
    acodec: str = ""
    aprofile: str = ""
    sample_rate: int = 0
    channels: int = 0
    abitrate: int = 0
    extra_streams: int = 0
    raw: dict = field(default_factory=dict)

    @property
    def is_hdr(self) -> bool:
        return self.color_transfer in HDR_TRANSFERS

    @property
    def display_size(self) -> Tuple[int, int]:
        if self.rotation in (90, -90, 270, -270):
            return self.height, self.width
        return self.width, self.height

    def summary_lines(self) -> List[str]:
        w, h = self.display_size
        lines = [
            f"file      : {self.path.name}  ({human_size(self.size)})",
            f"container : {self.container}   duration {self.duration:.2f}s   total {self.bitrate / 1e6:.2f} Mbps",
        ]
        if self.has_video:
            lines.append(
                f"video     : {self.vcodec} {self.vprofile or ''} L{self.vlevel or '?'}  {w}x{h}"
                f"  {self.fps:.3f} fps  {self.pix_fmt}  {self.vbitrate / 1e6:.2f} Mbps"
                + (f"  rotation {self.rotation}" if self.rotation else "")
                + (f"  HDR({self.color_transfer})" if self.is_hdr else "")
            )
            if self.nb_frames:
                lines.append(f"frames    : {self.nb_frames} declared  (avg {self.avg_fps:.2f} fps by count)")
        else:
            lines.append("video     : NONE")
        if self.has_audio:
            lines.append(f"audio     : {self.acodec} {self.aprofile}  {self.sample_rate} Hz  {self.channels} ch"
                         f"  {self.abitrate / 1e3:.0f} kbps")
        else:
            lines.append("audio     : none")
        return lines


def _frac(s: str) -> float:
    try:
        if "/" in s:
            a, b = s.split("/", 1)
            return float(a) / float(b) if float(b) else 0.0
        return float(s)
    except (ValueError, ZeroDivisionError):
        return 0.0


def probe(ffprobe: str, path: Path) -> Analysis:
    r = run([ffprobe, "-v", "error", "-print_format", "json", "-show_format", "-show_streams", str(path)], timeout=120)
    if r.returncode != 0:
        raise ToolError(f"ffprobe could not read {path.name}:\n{r.stderr.strip()[-800:]}")
    info = json.loads(r.stdout or "{}")
    an = Analysis(path=path, raw=info)
    fmt = info.get("format", {})
    an.size = int(fmt.get("size") or path.stat().st_size)
    an.container = fmt.get("format_name", "")
    an.duration = float(fmt.get("duration") or 0.0)
    an.bitrate = int(fmt.get("bit_rate") or 0)

    video = [s for s in info.get("streams", []) if s.get("codec_type") == "video"
             and s.get("disposition", {}).get("attached_pic", 0) == 0]
    audio = [s for s in info.get("streams", []) if s.get("codec_type") == "audio"]
    an.extra_streams = len(info.get("streams", [])) - len(video[:1]) - len(audio[:1])

    if video:
        v = video[0]
        an.has_video = True
        an.vcodec = v.get("codec_name", "")
        an.vprofile = v.get("profile", "") or ""
        try:
            an.vlevel = int(v.get("level") or 0)
        except (TypeError, ValueError):
            an.vlevel = 0
        an.width = int(v.get("width") or 0)
        an.height = int(v.get("height") or 0)
        an.fps = _frac(v.get("r_frame_rate", "0"))
        an.avg_fps = _frac(v.get("avg_frame_rate", "0"))
        an.nb_frames = int(v.get("nb_frames") or 0)
        an.pix_fmt = v.get("pix_fmt", "") or ""
        an.color_transfer = v.get("color_transfer", "") or ""
        an.color_primaries = v.get("color_primaries", "") or ""
        an.color_space = v.get("color_space", "") or ""
        an.field_order = v.get("field_order", "") or ""
        an.vbitrate = int(v.get("bit_rate") or 0)
        if not an.duration:
            an.duration = float(v.get("duration") or 0.0)
        rot = 0
        for sd in v.get("side_data_list", []) or []:
            if "rotation" in sd:
                try:
                    rot = int(float(sd["rotation"]))
                except (TypeError, ValueError):
                    rot = 0
        if not rot:
            try:
                rot = int(float(v.get("tags", {}).get("rotate", 0)))
            except (TypeError, ValueError):
                rot = 0
        an.rotation = rot % 360 if rot else 0
        if an.rotation == 270:
            an.rotation = -90
    if audio:
        a = audio[0]
        an.has_audio = True
        an.acodec = a.get("codec_name", "")
        an.aprofile = a.get("profile", "") or ""
        an.sample_rate = int(a.get("sample_rate") or 0)
        an.channels = int(a.get("channels") or 0)
        an.abitrate = int(a.get("bit_rate") or 0)
    return an


@dataclass
class Decision:
    video: str          # "copy" | "encode"
    audio: str          # "copy" | "encode" | "none"
    reasons: List[str]
    warnings: List[str]


def decide(an: Analysis, force_encode: bool = False, no_encode: bool = False) -> Decision:
    reasons: List[str] = []
    warnings: List[str] = []
    if not an.has_video:
        raise ToolError("the file has no video stream")

    w, h = an.display_size
    if an.vcodec != "h264":
        reasons.append(f"video codec is {an.vcodec}, TikTok pass-through is only proven with H.264")
    if an.pix_fmt and an.pix_fmt != "yuv420p":
        reasons.append(f"pixel format is {an.pix_fmt} (needs 8-bit yuv420p)")
    if an.is_hdr:
        reasons.append(f"source is HDR ({an.color_transfer}); it will be tone-mapped to SDR (colors shift slightly)")
    if max(an.width, an.height) > 1920:
        reasons.append(f"resolution {w}x{h} is above 1080p; it will be scaled to fit 1920")
    if an.fps > TIKTOK_MAX_FPS + 0.5:
        reasons.append(f"frame rate {an.fps:.2f} is above 60; it will be converted to 60")
    if an.field_order and an.field_order not in ("progressive", "unknown"):
        reasons.append(f"video is interlaced ({an.field_order})")
    if an.vcodec == "h264" and an.vprofile and an.vprofile.lower() not in ("baseline", "constrained baseline", "main", "high"):
        reasons.append(f"H.264 profile {an.vprofile} is unusual for TikTok (proven files use High)")

    video = "encode" if (reasons or force_encode) else "copy"
    if force_encode and not reasons:
        reasons.append("--force-encode requested")

    if not an.has_audio:
        audio = "none"
        warnings.append("no audio track; TikTok accepts silent video but the sound picker will be empty")
    elif an.acodec != "aac" or an.channels > 2 or an.sample_rate not in (44100, 48000):
        audio = "encode"
        reasons.append(f"audio {an.acodec} {an.channels}ch {an.sample_rate}Hz will be re-encoded to AAC stereo")
    else:
        audio = "copy"

    if no_encode:
        if video == "encode":
            raise ToolError("--no-encode was given, but the source needs encoding:\n  - " + "\n  - ".join(reasons))
        audio = "copy" if audio == "encode" else audio

    # Official-limit warnings (not blockers)
    if an.size > TIKTOK_MAX_FILE_BYTES:
        warnings.append("file is above TikTok's 4 GB upload limit")
    if an.fps and an.fps < TIKTOK_MIN_FPS - 0.5:
        warnings.append(f"frame rate {an.fps:.2f} is below TikTok's documented minimum of 23 fps")
    if min(w, h) and min(w, h) < TIKTOK_MIN_SIDE:
        warnings.append(f"resolution {w}x{h} is below TikTok's documented minimum of 360 px")
    if an.has_video and (w, h) not in ((1080, 1920), (1920, 1080)) and max(w, h) <= 1920:
        warnings.append(f"{w}x{h} is not 1080x1920; TikTok will letterbox non-9:16 video (not a quality issue)")
    if an.vcodec == "h264" and an.vlevel and an.vlevel > 42:
        warnings.append(f"H.264 level {an.vlevel / 10:.1f} is above 4.2; proven pass-through files used level 4.2")
    return Decision(video=video, audio=audio, reasons=reasons, warnings=warnings)


# --------------------------------------------------------------------------- #
# ffmpeg encode / remux
# --------------------------------------------------------------------------- #

_filters_cache: Optional[str] = None


def ffmpeg_has_filter(ffmpeg: str, name: str) -> bool:
    global _filters_cache
    if _filters_cache is None:
        r = run([ffmpeg, "-hide_banner", "-filters"], timeout=60)
        _filters_cache = r.stdout or ""
    return f" {name} " in _filters_cache


def build_ffmpeg_cmd(ffmpeg: str, an: Analysis, dec: Decision, dst: Path,
                     crf: int, preset: str, maxrate_mbps: int) -> List[str]:
    cmd = [ffmpeg, "-hide_banner", "-nostdin", "-y", "-i", str(an.path),
           "-map", "0:V:0"]
    if dec.audio != "none":
        cmd += ["-map", "0:a:0"]
    cmd += ["-map_metadata", "-1", "-map_chapters", "-1", "-dn", "-sn"]

    if dec.video == "encode":
        vf: List[str] = []
        if an.is_hdr:
            if ffmpeg_has_filter(ffmpeg, "zscale") and ffmpeg_has_filter(ffmpeg, "tonemap"):
                # tell zscale what the source is, in case the frames carry no color tags
                tin = {"smpte2084": "smpte2084", "arib-std-b67": "arib-std-b67"}.get(an.color_transfer, "smpte2084")
                pin = "bt2020" if an.color_primaries in ("", "bt2020") else an.color_primaries
                min_ = "bt2020nc" if an.color_space in ("", "bt2020nc", "bt2020c") else an.color_space
                vf.append(f"zscale=tin={tin}:pin={pin}:min={min_}:t=linear:npl=100,format=gbrpf32le,"
                          "zscale=p=bt709,tonemap=tonemap=hable:desat=0,"
                          "zscale=t=bt709:m=bt709:r=tv,format=yuv420p")
            else:
                vf.append("format=yuv420p")
        vf.append("scale=w='if(gt(iw,ih),min(iw,1920),-2)':h='if(gt(iw,ih),-2,min(ih,1920))':flags=lanczos")
        if an.fps > TIKTOK_MAX_FPS + 0.5:
            vf.append("fps=60")
        fps_for_gop = min(an.fps or 30.0, 60.0)
        gop = max(24, int(round(fps_for_gop * 2)))
        if preset in ("ultrafast", "superfast"):
            preset = "veryfast"  # faster presets drop CABAC/8x8dct and produce a Baseline stream
        cmd += ["-vf", ",".join(vf),
                "-c:v", "libx264", "-preset", preset, "-crf", str(crf),
                "-profile:v", "high", "-level:v", "4.2", "-pix_fmt", "yuv420p",
                "-maxrate", f"{maxrate_mbps}M", "-bufsize", f"{maxrate_mbps * 2}M",
                "-g", str(gop), "-keyint_min", str(gop // 2),
                "-x264-params", "open-gop=0"]
        if an.is_hdr:
            cmd += ["-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709"]
    else:
        cmd += ["-c:v", "copy"]

    if dec.audio == "encode":
        cmd += ["-c:a", "aac", "-b:a", "256k", "-ar", "48000" if an.sample_rate != 44100 else "44100", "-ac", "2"]
    elif dec.audio == "copy":
        cmd += ["-c:a", "copy"]

    cmd += ["-movflags", "+faststart", "-brand", "isom", "-f", "mp4", str(dst)]
    return cmd


def ffmpeg_stage(ffmpeg: str, an: Analysis, dec: Decision, dst: Path, crf: int, preset: str, maxrate: int) -> None:
    cmd = build_ffmpeg_cmd(ffmpeg, an, dec, dst, crf, preset, maxrate)
    say("  " + ("encoding with libx264 ..." if dec.video == "encode" else "lossless remux (-c copy) ..."))
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                            encoding="utf-8", errors="replace")
    last_progress = ""
    tail: List[str] = []
    assert proc.stderr is not None
    for line in proc.stderr:
        line = line.rstrip("\r\n")
        if not line:
            continue
        tail.append(line)
        tail = tail[-40:]
        if line.startswith("frame=") or "time=" in line:
            last_progress = line.strip()
            print("\r  " + last_progress[:100].ljust(100), end="", flush=True)
    proc.wait()
    if last_progress:
        print()
    if proc.returncode != 0 or not dst.is_file() or dst.stat().st_size == 0:
        raise ToolError("ffmpeg failed:\n" + "\n".join(tail[-12:]))


# --------------------------------------------------------------------------- #
# Minimal ISO-BMFF (MP4) parser
# --------------------------------------------------------------------------- #

CONTAINER_BOXES = {"moov", "trak", "mdia", "minf", "stbl", "edts", "dinf", "mvex", "udta"}


@dataclass(frozen=True)
class Box:
    start: int
    size: int
    type: str
    hdr: int

    @property
    def end(self) -> int:
        return self.start + self.size

    @property
    def body(self) -> int:
        return self.start + self.hdr


def u32(d: bytes, o: int) -> int:
    return struct.unpack_from(">I", d, o)[0]


def u64(d: bytes, o: int) -> int:
    return struct.unpack_from(">Q", d, o)[0]


def p32(v: int) -> bytes:
    return struct.pack(">I", v)


def p64(v: int) -> bytes:
    return struct.pack(">Q", v)


def iter_boxes(d: bytes, start: int, end: int):
    pos = start
    while pos < end:
        if pos + 8 > end:
            raise MP4Error(f"truncated box header at {pos}")
        size32 = u32(d, pos)
        typ = d[pos + 4:pos + 8].decode("latin-1")
        hdr = 8
        if size32 == 1:
            if pos + 16 > end:
                raise MP4Error(f"truncated 64-bit box at {pos}")
            size = u64(d, pos + 8)
            hdr = 16
        elif size32 == 0:
            size = end - pos
        else:
            size = size32
        if size < hdr or pos + size > end:
            raise MP4Error(f"invalid box {typ!r} at {pos} (size {size})")
        yield Box(pos, size, typ, hdr)
        pos += size


def children(d: bytes, box: Box) -> List[Box]:
    return list(iter_boxes(d, box.body, box.end))


def child(d: bytes, box: Box, typ: str) -> Optional[Box]:
    for c in iter_boxes(d, box.body, box.end):
        if c.type == typ:
            return c
    return None


def path_box(d: bytes, box: Box, path: List[str]) -> Optional[Box]:
    cur: Optional[Box] = box
    for t in path:
        if cur is None:
            return None
        cur = child(d, cur, t)
    return cur


def make_box(typ: str, payload: bytes) -> bytes:
    size = 8 + len(payload)
    if size >= 1 << 32:
        return p32(1) + typ.encode("latin-1") + p64(16 + len(payload)) + payload
    return p32(size) + typ.encode("latin-1") + payload


def handler_of(d: bytes, trak: Box) -> str:
    hdlr = path_box(d, trak, ["mdia", "hdlr"])
    if hdlr is None or hdlr.body + 12 > hdlr.end:
        return ""
    return d[hdlr.body + 8:hdlr.body + 12].decode("latin-1")


def find_trak(d: bytes, moov: Box, handler: str) -> Optional[Box]:
    for t in children(d, moov):
        if t.type == "trak" and handler_of(d, t) == handler:
            return t
    return None


def sample_entry_fourcc(d: bytes, stbl: Box) -> str:
    stsd = child(d, stbl, "stsd")
    if stsd is None or stsd.body + 16 > stsd.end:
        return ""
    return d[stsd.body + 12:stsd.body + 16].decode("latin-1")


def parse_stts(d: bytes, b: Box) -> List[Tuple[int, int]]:
    n = u32(d, b.body + 4)
    return [(u32(d, b.body + 8 + i * 8), u32(d, b.body + 12 + i * 8)) for i in range(n)]


def parse_stsz(d: bytes, b: Box) -> List[int]:
    uniform = u32(d, b.body + 4)
    n = u32(d, b.body + 8)
    if uniform:
        return [uniform] * n
    return list(struct.unpack_from(f">{n}I", d, b.body + 12))


def parse_stsc(d: bytes, b: Box) -> List[Tuple[int, int, int]]:
    n = u32(d, b.body + 4)
    return [tuple(struct.unpack_from(">III", d, b.body + 8 + i * 12)) for i in range(n)]  # type: ignore


def parse_chunk_offsets(d: bytes, b: Box) -> List[int]:
    n = u32(d, b.body + 4)
    if b.type == "co64":
        return list(struct.unpack_from(f">{n}Q", d, b.body + 8))
    return list(struct.unpack_from(f">{n}I", d, b.body + 8))


def parse_ctts(d: bytes, b: Box) -> Tuple[int, List[Tuple[int, int]]]:
    version = d[b.body]
    n = u32(d, b.body + 4)
    fmt = ">Ii" if version == 1 else ">II"
    return version, [tuple(struct.unpack_from(fmt, d, b.body + 8 + i * 8)) for i in range(n)]  # type: ignore


def build_stts(entries: List[Tuple[int, int]]) -> bytes:
    return make_box("stts", p32(0) + p32(len(entries)) + b"".join(p32(c) + p32(dlt) for c, dlt in entries))


def build_stsz(sizes: List[int]) -> bytes:
    return make_box("stsz", p32(0) + p32(0) + p32(len(sizes)) + struct.pack(f">{len(sizes)}I", *sizes))


def build_stsc(entries: List[Tuple[int, int, int]]) -> bytes:
    return make_box("stsc", p32(0) + p32(len(entries)) + b"".join(struct.pack(">III", *e) for e in entries))


def build_chunk_offsets(offsets: List[int], use64: bool) -> bytes:
    if use64:
        return make_box("co64", p32(0) + p32(len(offsets)) + struct.pack(f">{len(offsets)}Q", *offsets))
    return make_box("stco", p32(0) + p32(len(offsets)) + struct.pack(f">{len(offsets)}I", *offsets))


def build_ctts(version: int, entries: List[Tuple[int, int]]) -> bytes:
    fmt = ">Ii" if version == 1 else ">II"
    return make_box("ctts", bytes([version, 0, 0, 0]) + p32(len(entries)) + b"".join(struct.pack(fmt, *e) for e in entries))


def build_ftyp() -> bytes:
    return make_box("ftyp", b"isom" + p32(512) + b"isomiso2avc1mp41")


def mdat_payload_ranges(d: bytes) -> List[Tuple[int, int]]:
    return [(b.body, b.end) for b in iter_boxes(d, 0, len(d)) if b.type == "mdat"]


def media_sha256(d: bytes, strip_ghost_tail: bool = False) -> str:
    """Hash of every mdat payload. The 8-byte ghost filler tail can be excluded."""
    h = hashlib.sha256()
    ranges = mdat_payload_ranges(d)
    if not ranges:
        raise MP4Error("no mdat box")
    for i, (s, e) in enumerate(ranges):
        if strip_ghost_tail and i == len(ranges) - 1 and d[e - 8:e] == GHOST_SAMPLE:
            e -= 8
        h.update(d[s:e])
    return h.hexdigest()


# --------------------------------------------------------------------------- #
# Patch 1: ghost frames (sample-table inflation)  -- default
# --------------------------------------------------------------------------- #

def _strip_nalus(sample: bytes, drop_types=(6, 9)) -> bytes:
    """Drop SEI (6) and AUD (9) NAL units from a 4-byte-length-prefixed AVC sample."""
    out = bytearray()
    pos = 0
    while pos + 4 <= len(sample):
        n = u32(sample, pos)
        if n == 0 or pos + 4 + n > len(sample):
            return sample  # malformed, leave untouched
        nal_type = sample[pos + 4] & 0x1F
        if nal_type not in drop_types:
            out += sample[pos:pos + 4 + n]
        pos += 4 + n
    return bytes(out) if out else sample


def ghost_patch(d: bytes, multiplier: int = 10, replica: bool = False) -> Tuple[bytes, dict]:
    """
    Declare `multiplier`x as many video samples as really exist. The extra
    samples are 8-byte fillers that all point at ONE block appended inside
    mdat. Real video/audio bytes are untouched (replica=False) so the served
    file is literally the original encode.
    """
    if multiplier < 2:
        raise ToolError("multiplier must be >= 2")
    top = list(iter_boxes(d, 0, len(d)))
    moov = next((b for b in top if b.type == "moov"), None)
    mdats = [b for b in top if b.type == "mdat"]
    if moov is None or len(mdats) != 1:
        raise MP4Error("expected one moov and exactly one mdat (file was not normalized)")
    mdat = mdats[0]
    if moov.start > mdat.start:
        raise MP4Error("moov must come before mdat (faststart)")
    vtrak = find_trak(d, moov, "vide")
    if vtrak is None:
        raise MP4Error("no video track")
    stbl = path_box(d, vtrak, ["mdia", "minf", "stbl"])
    if stbl is None:
        raise MP4Error("video track has no sample table")
    fourcc = sample_entry_fourcc(d, stbl)
    if fourcc not in ("avc1", "avc3"):
        raise MP4Error(f"ghost patch supports H.264 (avc1) only, got {fourcc!r}; run without --no-encode")

    stts_b, stsz_b, stsc_b = child(d, stbl, "stts"), child(d, stbl, "stsz"), child(d, stbl, "stsc")
    co_b = child(d, stbl, "stco") or child(d, stbl, "co64")
    ctts_b, sdtp_b = child(d, stbl, "ctts"), child(d, stbl, "sdtp")
    if not (stts_b and stsz_b and stsc_b and co_b):
        raise MP4Error("video sample table is incomplete (stts/stsz/stsc/stco)")
    if child(d, stbl, "stz2"):
        raise MP4Error("stz2 sample sizes are not supported")

    stts = parse_stts(d, stts_b)
    sizes = parse_stsz(d, stsz_b)
    stsc = parse_stsc(d, stsc_b)
    v_offsets = parse_chunk_offsets(d, co_b)
    real = len(sizes)
    if real == 0 or not stts or not stsc or not v_offsets:
        raise MP4Error("video track has no samples")
    if sum(c for c, _ in stts) != real:
        raise MP4Error("stts sample count does not match stsz")

    # ghost frame timing = the most common real frame duration
    by_delta: Dict[int, int] = {}
    for c, dlt in stts:
        by_delta[dlt] = by_delta.get(dlt, 0) + c
    delta = max(by_delta.items(), key=lambda kv: kv[1])[0]
    ghost = real * (multiplier - 1)

    # --- mdat rebuild (optionally strip SEI/AUD from the first video sample)
    payload = d[mdat.body:mdat.end]
    first_abs = v_offsets[0]
    first_rel = first_abs - mdat.body
    if first_rel < 0 or first_rel + sizes[0] > len(payload):
        raise MP4Error("first video sample is outside mdat")
    removed = 0
    new_sizes = list(sizes)
    if replica:
        first = payload[first_rel:first_rel + sizes[0]]
        stripped = _strip_nalus(first)
        removed = len(first) - len(stripped)
        if removed:
            payload = payload[:first_rel] + stripped + payload[first_rel + sizes[0]:]
            new_sizes[0] = len(stripped)
    new_payload = payload + GHOST_SAMPLE
    mdat_hdr = 16 if 8 + len(new_payload) >= (1 << 32) else 8
    new_mdat = (p32(1) + b"mdat" + p64(16 + len(new_payload)) if mdat_hdr == 16
                else p32(8 + len(new_payload)) + b"mdat") + new_payload

    # --- new video sample tables
    new_stts = build_stts(stts + [(ghost, delta)])
    new_stsz = build_stsz(new_sizes + [len(GHOST_SAMPLE)] * ghost)
    last_sdi = stsc[-1][2]
    new_stsc = build_stsc(stsc + [(len(v_offsets) + 1, 1, last_sdi)])
    new_ctts = None
    if ctts_b:
        ver, entries = parse_ctts(d, ctts_b)
        new_ctts = build_ctts(ver, entries + [(ghost, 0)])
    new_sdtp = None
    if sdtp_b:
        new_sdtp = make_box("sdtp", d[sdtp_b.body:sdtp_b.end] + b"\x00" * ghost)
    use64 = any(o >= (1 << 32) for o in v_offsets) or co_b.type == "co64" or len(d) + 8 * ghost >= (1 << 32) - (1 << 20)

    ftyp = build_ftyp()
    free = p32(8) + b"free"

    def rebuild_moov(remap: Callable[[int], int], pad_abs: int) -> bytes:
        def walk(box: Box, role: str) -> bytes:
            if box.type == "moov":
                parts = []
                for c in children(d, box):
                    if c.type == "trak":
                        parts.append(walk(c, handler_of(d, c)))
                    elif c.type == "udta" and replica:
                        continue
                    else:
                        parts.append(d[c.start:c.end])
                if replica:
                    parts.append(_udta_comment("TikTokHQ"))
                return make_box("moov", b"".join(parts))
            if box.type in ("trak", "mdia", "minf", "stbl"):
                return make_box(box.type, b"".join(walk(c, role) for c in children(d, box)))
            # leaves
            if box.type in ("stco", "co64"):
                offs = [remap(o) for o in parse_chunk_offsets(d, box)]
                if role == "vide" and box.start == co_b.start:
                    offs += [pad_abs] * ghost
                    return build_chunk_offsets(offs, use64)
                return build_chunk_offsets(offs, box.type == "co64" or any(o >= (1 << 32) for o in offs))
            if role == "vide" and box.start == stts_b.start:
                return new_stts
            if role == "vide" and box.start == stsz_b.start:
                return new_stsz
            if role == "vide" and box.start == stsc_b.start:
                return new_stsc
            if role == "vide" and ctts_b and box.start == ctts_b.start and new_ctts:
                return new_ctts
            if role == "vide" and sdtp_b and box.start == sdtp_b.start and new_sdtp:
                return new_sdtp
            if replica and role == "soun" and box.type == "hdlr":
                return make_box("hdlr", p32(0) + p32(0) + b"soun" + b"\x00" * 12 + b"SoundHandler\x00")
            if replica and role == "vide" and box.type == "hdlr":
                return make_box("hdlr", p32(0) + p32(0) + b"vide" + b"\x00" * 12 + b"VideoHandler\x00")
            return d[box.start:box.end]
        return walk(moov, "")

    # pass 1: size only
    moov1 = rebuild_moov(lambda o: o, 0)
    new_mdat_body = len(ftyp) + len(free) + len(moov1) + mdat_hdr
    shift = new_mdat_body - mdat.body
    pad_abs = new_mdat_body + len(new_payload) - len(GHOST_SAMPLE)

    def remap(o: int) -> int:
        n = o + shift
        if removed and o > first_abs:
            n -= removed
        return n

    moov2 = rebuild_moov(remap, pad_abs)
    if len(moov2) != len(moov1):
        raise MP4Error("internal error: moov size changed between passes")
    out = ftyp + free + moov2 + new_mdat
    stats = {
        "method": "ghost",
        "real_frames": real,
        "ghost_frames": ghost,
        "declared_frames": real + ghost,
        "multiplier": multiplier,
        "frame_delta": delta,
        "sei_bytes_removed": removed,
        "bytes_added": len(out) - len(d),
    }
    return out, stats


def _udta_comment(comment: str) -> bytes:
    hdlr = make_box("hdlr", p32(0) + p32(0) + b"mdir" + b"appl" + b"\x00" * 9)
    data = make_box("data", p32(1) + p32(0) + comment.encode("utf-8"))
    cmt = make_box("\xa9cmt", data)
    ilst = make_box("ilst", cmt)
    meta = make_box("meta", p32(0) + hdlr + ilst)
    return make_box("udta", meta)


# --------------------------------------------------------------------------- #
# Patch 2: elst version trick (fps-method family, H.264 variant)
# --------------------------------------------------------------------------- #

def elst_patch(d: bytes) -> Tuple[bytes, dict]:
    """
    Set the video track's edit list (elst) version/flags to 0x10000001 (an
    invalid version). Players ignore an edit list they cannot read; the
    community reports TikTok's transcoder gives up and keeps the source.
    If the track has no edit list, a plain one is inserted first.
    """
    top = list(iter_boxes(d, 0, len(d)))
    moov = next((b for b in top if b.type == "moov"), None)
    mdats = [b for b in top if b.type == "mdat"]
    if moov is None or len(mdats) != 1 or moov.start > mdats[0].start:
        raise MP4Error("expected faststart file with one moov and one mdat")
    vtrak = find_trak(d, moov, "vide")
    if vtrak is None:
        raise MP4Error("no video track")
    tkhd = child(d, vtrak, "tkhd")
    if tkhd is None:
        raise MP4Error("video track has no tkhd")
    edts = child(d, vtrak, "edts")

    inserted = False
    if edts is None:
        ver = d[tkhd.body]
        dur = u64(d, tkhd.body + 28) if ver == 1 else u32(d, tkhd.body + 20)
        elst = make_box("elst", p32(0) + p32(1) + p32(dur & 0xFFFFFFFF) + p32(0) + p32(0x00010000))
        edts_bytes = make_box("edts", elst)
        inserted = True
    else:
        edts_bytes = d[edts.start:edts.end]
    elst_off = edts_bytes.find(b"elst")
    if elst_off < 0:
        raise MP4Error("edts without elst")
    eb = bytearray(edts_bytes)
    eb[elst_off + 4:elst_off + 8] = b"\x10\x00\x00\x01"
    edts_bytes = bytes(eb)

    def walk(box: Box) -> bytes:
        if box.type == "moov":
            return make_box("moov", b"".join(walk(c) if c.type == "trak" else d[c.start:c.end] for c in children(d, box)))
        if box.type == "trak" and box.start == vtrak.start:
            parts = []
            for c in children(d, box):
                if c.type == "edts":
                    parts.append(edts_bytes)
                else:
                    parts.append(d[c.start:c.end])
                    if c.type == "tkhd" and inserted:
                        parts.append(edts_bytes)
            return make_box("trak", b"".join(parts))
        return d[box.start:box.end]

    new_moov = walk(moov)
    delta = len(new_moov) - moov.size
    out = bytearray(d[:moov.start] + new_moov + d[moov.end:])
    if delta:
        _shift_chunk_offsets(out, moov.start, moov.start + len(new_moov), delta)
    return bytes(out), {"method": "elst", "elst_inserted": inserted, "bytes_added": delta}


def _shift_chunk_offsets(buf: bytearray, start: int, end: int, delta: int) -> None:
    for box in iter_boxes(bytes(buf), start, end):
        if box.type in ("stco", "co64"):
            n = u32(buf, box.body + 4)
            step = 8 if box.type == "co64" else 4
            for i in range(n):
                o = box.body + 8 + i * step
                if step == 8:
                    struct.pack_into(">Q", buf, o, u64(buf, o) + delta)
                else:
                    v = u32(buf, o) + delta
                    if v >= 1 << 32:
                        raise MP4Error("stco overflow")
                    struct.pack_into(">I", buf, o, v)
        elif box.type in CONTAINER_BOXES:
            _shift_chunk_offsets(buf, box.body, box.end, delta)


# --------------------------------------------------------------------------- #
# Patch 3: fps/timescale trick (ut0ku "120fps method")
# --------------------------------------------------------------------------- #

def fps_patch(d: bytes, divisor: int) -> Tuple[bytes, dict]:
    """Divide mvhd and every mdhd timescale+duration by `divisor` (2 for 60 fps, 4 for 120)."""
    if divisor not in (2, 4):
        raise ToolError("fps method: divisor must be 2 (60 fps source) or 4 (120 fps source)")
    buf = bytearray(d)
    moov = next((b for b in iter_boxes(d, 0, len(d)) if b.type == "moov"), None)
    if moov is None:
        raise MP4Error("no moov")
    patched = []

    def patch_header(box: Box) -> None:
        ver = buf[box.body]
        if ver == 0:
            ts_o, dur_o = box.body + 12, box.body + 16
            ts, dur = u32(buf, ts_o), u32(buf, dur_o)
            struct.pack_into(">I", buf, ts_o, max(1, ts // divisor))
            struct.pack_into(">I", buf, dur_o, dur // divisor)
        else:
            ts_o, dur_o = box.body + 20, box.body + 24
            ts, dur = u32(buf, ts_o), u64(buf, dur_o)
            struct.pack_into(">I", buf, ts_o, max(1, ts // divisor))
            struct.pack_into(">Q", buf, dur_o, dur // divisor)
        patched.append(f"{box.type}: timescale {ts}->{max(1, ts // divisor)}")

    mvhd = child(d, moov, "mvhd")
    if mvhd:
        patch_header(mvhd)
    for trak in children(d, moov):
        if trak.type != "trak":
            continue
        mdhd = path_box(d, trak, ["mdia", "mdhd"])
        if mdhd:
            patch_header(mdhd)
    return bytes(buf), {"method": "fps", "divisor": divisor, "patched": patched, "bytes_added": 0}


# --------------------------------------------------------------------------- #
# Verification
# --------------------------------------------------------------------------- #

# Messages ffmpeg prints when it reaches the 8-byte ghost samples (expected).
GHOST_NOISE = ("missing picture in access unit with size 8", "no frame!",
               "Error submitting packet to decoder", "Invalid data found when processing input",
               "Last message repeated")


def is_ghost_noise(line: str) -> bool:
    return any(s in line for s in GHOST_NOISE)


def verify_output(ffmpeg: str, ffprobe: str, out: Path, staged: Path, method: str, seconds: float) -> List[str]:
    notes: List[str] = []
    an = probe(ffprobe, out)
    if not an.has_video or an.vcodec != "h264":
        raise ToolError(f"verification failed: output video is {an.vcodec or 'missing'}")
    notes.append(f"ffprobe   : OK  {an.vcodec} {an.width}x{an.height} {an.fps:.3f} fps, "
                 f"{an.nb_frames} declared frames, duration {an.duration:.2f}s")
    # decode the beginning of the real stream (never past the real frames: the
    # ghost samples that follow are meant to be undecodable)
    staged_an = probe(ffprobe, staged)
    real_dur = staged_an.duration or an.duration
    window = max(0.5, min(seconds, real_dur - 0.1))
    r = run([ffmpeg, "-hide_banner", "-v", "error", "-nostdin", "-nostats", "-progress", "pipe:1",
             "-i", str(out), "-t", f"{window:.2f}", "-map", "0:V:0", "-f", "null", "-"], timeout=600)
    if r.returncode != 0:
        raise ToolError("verification failed: ffmpeg could not decode the output:\n" + r.stderr[-600:])
    decoded = 0
    for line in r.stdout.splitlines():
        if line.startswith("frame="):
            try:
                decoded = int(line.split("=", 1)[1].strip())
            except ValueError:
                pass
    expected = int(window * (staged_an.fps or an.fps))
    errs = [l for l in r.stderr.splitlines() if l.strip() and not is_ghost_noise(l)]
    if decoded < max(1, expected - 3):
        raise ToolError(f"verification failed: decoded {decoded} frames, expected about {expected}:\n" + r.stderr[-600:])
    notes.append(f"decode    : OK  {decoded} frames ({window:.1f}s) decode cleanly"
                 + (f" ({len(errs)} warnings)" if errs else ""))
    # media bytes unchanged?
    with open(staged, "rb") as f:
        staged_bytes = f.read()
    with open(out, "rb") as f:
        out_bytes = f.read()
    if method == "ghost":
        same = media_sha256(staged_bytes) == media_sha256(out_bytes, strip_ghost_tail=True)
    else:
        same = media_sha256(staged_bytes) == media_sha256(out_bytes)
    notes.append("media     : " + ("OK  video/audio bytes are byte-identical to the staged file"
                                   if same else "CHANGED (expected only with --replica)"))
    return notes


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

METHODS = ("ghost", "elst", "fps")


def output_path_for(src: Path, method: str, multi: bool, out_dir: Optional[Path]) -> Path:
    base = out_dir if out_dir else src.parent
    tag = OUTPUT_SUFFIX + (f"_{method}" if multi else "")
    return base / f"{src.stem}{tag}.mp4"


def cmd_prep(args: argparse.Namespace) -> int:
    ffmpeg, ffprobe = require_tools()
    src = Path(args.input).expanduser().resolve()
    if not src.is_file():
        raise ToolError(f"file not found: {src}")
    if OUTPUT_SUFFIX in src.stem:
        say(f"!! {src.name} already looks like a TikTok HQ output; processing it again anyway")

    methods = list(METHODS) if args.method == "all" else [args.method]
    multi = len(methods) > 1
    out_dir = Path(args.out_dir).expanduser().resolve() if args.out_dir else None
    if out_dir:
        out_dir.mkdir(parents=True, exist_ok=True)

    say(f"TikTok HQ v{VERSION}")
    say("=" * 72)
    an = probe(ffprobe, src)
    for line in an.summary_lines():
        say("  " + line)
    dec = decide(an, force_encode=args.force_encode, no_encode=args.no_encode)
    say("-" * 72)
    say(f"  plan      : video={dec.video}  audio={dec.audio}  method={'+'.join(methods)}  x{args.multiplier}")
    for r in dec.reasons:
        say(f"  why       : {r}")
    for w in dec.warnings:
        say(f"  note      : {w}")
    say("-" * 72)

    with tempfile.TemporaryDirectory(prefix="tiktokhq_") as tmp:
        staged = Path(tmp) / "staged.mp4"
        say("[1/3] stage (ffmpeg)")
        ffmpeg_stage(ffmpeg, an, dec, staged, args.crf, args.preset, args.maxrate)
        staged_an = probe(ffprobe, staged)
        say(f"  staged    : {staged_an.vcodec} {staged_an.width}x{staged_an.height} {staged_an.fps:.3f} fps "
            f"{staged_an.pix_fmt} {human_size(staged_an.size)}  {staged_an.vbitrate / 1e6:.2f} Mbps video")
        with open(staged, "rb") as f:
            staged_bytes = f.read()

        results: List[Tuple[Path, dict]] = []
        for method in methods:
            say(f"[2/3] patch ({method})")
            if method == "ghost":
                out_bytes, stats = ghost_patch(staged_bytes, args.multiplier, args.replica)
            elif method == "elst":
                out_bytes, stats = elst_patch(staged_bytes)
            else:
                fps = round(staged_an.fps)
                divisor = 4 if fps >= 100 else 2
                if fps not in (60, 120) and not args.fps_divisor:
                    say(f"  !! fps method expects a 60 or 120 fps source (this is {staged_an.fps:.2f}); using divisor 2")
                out_bytes, stats = fps_patch(staged_bytes, args.fps_divisor or divisor)
            out = output_path_for(src, method, multi, out_dir)
            if out.resolve() == src.resolve():
                raise ToolError("output path equals input path")
            with open(out, "wb") as f:
                f.write(out_bytes)
            for k, v in stats.items():
                say(f"  {k:<18}: {v}")
            say(f"[3/3] verify ({method})")
            for n in verify_output(ffmpeg, ffprobe, out, staged, method, args.verify_seconds):
                say("  " + n)
            results.append((out, stats))

    say("=" * 72)
    for out, _ in results:
        say(f"DONE -> {out}  ({human_size(out.stat().st_size)})")
    say("")
    say("Next: upload from a PC browser at https://www.tiktok.com/upload (not the phone app).")
    say("      Keep 'Allow high-quality uploads' ON if the option is shown.")
    say("      After it is live, open the video's quality menu and look for 'Original'.")
    return 0


def cmd_check(args: argparse.Namespace) -> int:
    _, ffprobe = require_tools()
    src = Path(args.input).expanduser().resolve()
    if not src.is_file():
        raise ToolError(f"file not found: {src}")
    an = probe(ffprobe, src)
    say(f"TikTok HQ v{VERSION} - check")
    say("=" * 72)
    for line in an.summary_lines():
        say("  " + line)
    say("-" * 72)
    try:
        dec = decide(an)
        say(f"  plan      : video={dec.video}  audio={dec.audio}")
        for r in dec.reasons:
            say(f"  why       : {r}")
        for w in dec.warnings:
            say(f"  note      : {w}")
    except ToolError as e:
        say(f"  !! {e}")
    # ghost-frame signature?
    if an.nb_frames and an.fps and an.avg_fps > 2.5 * an.fps:
        say(f"  signature : declared frame density {an.avg_fps / an.fps:.1f}x the real frame rate "
            "(ghost-frame patched file)")
    else:
        say("  signature : no ghost-frame patch detected")
    say(f"  sha256    : {sha256_file(src)}")
    try:
        with open(src, "rb") as f:
            say(f"  media hash: {media_sha256(f.read(), True)}")
    except MP4Error:
        pass
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    _, ffprobe = require_tools()
    served = Path(args.served).expanduser().resolve()
    uploaded = Path(args.uploaded).expanduser().resolve()
    for p in (served, uploaded):
        if not p.is_file():
            raise ToolError(f"file not found: {p}")
    say(f"TikTok HQ v{VERSION} - compare")
    say("=" * 72)
    a, b = probe(ffprobe, served), probe(ffprobe, uploaded)
    say("SERVED (downloaded from TikTok)")
    for line in a.summary_lines():
        say("  " + line)
    say("UPLOADED (your TikTok HQ output)")
    for line in b.summary_lines():
        say("  " + line)
    say("-" * 72)
    verdict: str
    if sha256_file(served) == sha256_file(uploaded):
        verdict = "IDENTICAL: TikTok served your file byte-for-byte. This is 'Original' quality."
    else:
        try:
            with open(served, "rb") as f:
                sb = f.read()
            with open(uploaded, "rb") as f:
                ub = f.read()
            same_media = media_sha256(sb, True) == media_sha256(ub, True)
        except MP4Error:
            same_media = False
        if same_media:
            verdict = "SAME MEDIA: the container was rewritten but the video/audio bytes are identical (still original quality)."
        else:
            hints = []
            if a.vcodec != b.vcodec:
                hints.append(f"codec {b.vcodec} -> {a.vcodec}")
            if (a.width, a.height) != (b.width, b.height):
                hints.append(f"size {b.width}x{b.height} -> {a.width}x{a.height}")
            if b.vbitrate and a.vbitrate and abs(a.vbitrate - b.vbitrate) / b.vbitrate > 0.05:
                hints.append(f"video bitrate {b.vbitrate / 1e6:.2f} -> {a.vbitrate / 1e6:.2f} Mbps")
            if abs(a.fps - b.fps) > 0.5:
                hints.append(f"fps {b.fps:.2f} -> {a.fps:.2f}")
            verdict = "RE-ENCODED: TikTok transcoded this upload" + (f" ({'; '.join(hints)})" if hints else "") + "."
            verdict += "\n  Try another method: --method elst  or  --method all, then upload the variants."
    say(verdict)
    return 0


def pick_file_gui() -> Optional[str]:
    try:
        import tkinter as tk
        from tkinter import filedialog
    except Exception:
        return None
    try:
        root = tk.Tk()
        root.withdraw()
        try:
            root.attributes("-topmost", True)
        except Exception:
            pass
        p = filedialog.askopenfilename(
            title="TikTok HQ - choose a video",
            filetypes=[("Video", "*.mp4 *.mov *.m4v *.mkv *.webm *.avi *.mts *.ts"), ("All files", "*.*")])
        root.destroy()
        return p or None
    except Exception:
        return None


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tiktok_hq", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("-V", "--version", action="version", version=f"TikTok HQ {VERSION}")
    sub = p.add_subparsers(dest="cmd")

    prep = sub.add_parser("prep", help="prepare a video (default command)")
    _add_prep_args(prep)
    chk = sub.add_parser("check", help="analyze a file and show the plan")
    chk.add_argument("input")
    cmp_ = sub.add_parser("compare", help="compare the file TikTok serves with the file you uploaded")
    cmp_.add_argument("served")
    cmp_.add_argument("uploaded")
    return p


def _add_prep_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("input", nargs="?", help="source video (omit to open a file picker)")
    p.add_argument("--method", choices=METHODS + ("all",), default="ghost",
                   help="container patch to apply (default: ghost). 'all' writes one file per method for A/B testing")
    p.add_argument("--multiplier", type=int, default=10, help="ghost-frame multiplier (default 10)")
    p.add_argument("--replica", action="store_true",
                   help="also apply the cosmetic extras of the 'TikTok Enhancer' output (SEI strip, handler names, comment)")
    p.add_argument("--fps-divisor", type=int, choices=(2, 4), default=0, help="fps method: force divisor")
    p.add_argument("--force-encode", action="store_true", help="re-encode even if the source is already H.264")
    p.add_argument("--no-encode", action="store_true", help="never re-encode (fail if the source is not H.264)")
    p.add_argument("--crf", type=int, default=18, help="x264 quality when encoding (lower = better, default 18)")
    p.add_argument("--preset", default="medium", help="x264 preset when encoding (default medium)")
    p.add_argument("--maxrate", type=int, default=20, help="x264 max bitrate in Mbps when encoding (default 20)")
    p.add_argument("--out-dir", help="write outputs here instead of next to the source")
    p.add_argument("--verify-seconds", type=float, default=5.0, help="seconds to decode during verification")


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    interactive = False
    # Default command is "prep"; allow `tiktok_hq.py video.mp4` and `tiktok_hq.py --method ...`
    if argv and argv[0] not in ("prep", "check", "compare", "-h", "--help", "-V", "--version"):
        argv.insert(0, "prep")
    if not argv:
        argv = ["prep"]
        interactive = True
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        if args.cmd == "check":
            return cmd_check(args)
        if args.cmd == "compare":
            return cmd_compare(args)
        if not args.input:
            interactive = True
            picked = pick_file_gui()
            if not picked:
                try:
                    picked = input("Path of the video to prepare: ").strip().strip('"').strip("'")
                except EOFError:
                    picked = ""
            if not picked:
                say("no file chosen")
                return 2
            args.input = picked
        return cmd_prep(args)
    except ToolError as e:
        say(f"\nERROR: {e}")
        return 1
    except KeyboardInterrupt:
        say("\ncancelled")
        return 130
    finally:
        if interactive:
            try:
                input("\nPress Enter to close...")
            except EOFError:
                pass


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(errors="replace")  # type: ignore[attr-defined]
    except Exception:
        pass
    sys.exit(main())
