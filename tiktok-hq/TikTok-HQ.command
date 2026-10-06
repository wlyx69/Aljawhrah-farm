#!/bin/bash
# TikTok HQ - macOS launcher (double-click in Finder -> file picker)
# Command line: ./TikTok-HQ.command video.mp4 --method all
cd "$(dirname "$0")" || exit 1
PY="$(command -v python3 || command -v python)"
if [ -z "$PY" ]; then
    echo "Python 3 was not found. Install it with:  brew install python  (or from python.org)"
    read -r -p "Press Enter to close..."
    exit 1
fi
"$PY" "$(dirname "$0")/tiktok_hq.py" "$@"
RC=$?
if [ $# -gt 0 ]; then
    read -r -p "Press Enter to close..."
fi
exit $RC
