# -*- mode: python ; coding: utf-8 -*-
# PyInstaller build spec for the TikTok HQ desktop app.
#   Windows : dist/TikTokHQ.exe      (single file, ffmpeg + ffprobe inside)
#   macOS   : dist/TikTokHQ.app      (app bundle, ffmpeg + ffprobe inside)
# Expects bin/ffmpeg(.exe) and bin/ffprobe(.exe) next to this file; the GitHub
# workflow (.github/workflows/build-desktop.yml) downloads static builds there.
#
#   pip install pyinstaller
#   pyinstaller --noconfirm --clean TikTokHQ.spec
import re
import sys
from pathlib import Path

here = Path(SPECPATH).resolve()
is_win = sys.platform.startswith("win")
is_mac = sys.platform == "darwin"
ext = ".exe" if is_win else ""

version = re.search(r'^VERSION = "([^"]+)"', (here / "tiktok_hq.py").read_text(encoding="utf-8"), re.M).group(1)

binaries = []
for tool in ("ffmpeg", "ffprobe"):
    p = here / "bin" / f"{tool}{ext}"
    if not p.is_file():
        raise SystemExit(f"missing {p}: put a static {tool} build in bin/ first")
    binaries.append((str(p), "."))

a = Analysis(
    [str(here / "tiktok_hq_gui.py")],
    pathex=[str(here)],
    binaries=binaries,
    datas=[],
    hiddenimports=["tiktok_hq"],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

if is_win:
    exe = EXE(
        pyz, a.scripts, a.binaries, a.datas, [],
        name="TikTokHQ",
        debug=False, strip=False, upx=False,
        console=False, disable_windowed_traceback=False,
    )
else:
    exe = EXE(
        pyz, a.scripts, [],
        exclude_binaries=True,
        name="TikTokHQ",
        debug=False, strip=False, upx=False,
        console=False,
    )
    coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name="TikTokHQ")
    if is_mac:
        app = BUNDLE(
            coll,
            name="TikTokHQ.app",
            icon=None,
            bundle_identifier="farm.aljawhrah.tiktokhq",
            info_plist={
                "CFBundleName": "TikTok HQ",
                "CFBundleDisplayName": "TikTok HQ",
                "CFBundleShortVersionString": version,
                "CFBundleVersion": version,
                "NSHighResolutionCapable": True,
                "LSMinimumSystemVersion": "11.0",
            },
        )
