#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
TikTok HQ - desktop app entry point.

Opens the modern window (tiktok_hq_ui.py: a native web view showing ui/index.html).
If that cannot start (pywebview missing, no WebView2 runtime, page never loads),
falls back to the classic Tkinter window defined below and says why at the top
of its log. Shipped as a double-click application
by PyInstaller (TikTokHQ.spec, .github/workflows/build-desktop.yml); ffmpeg and
ffprobe ship inside the app.

  TikTokHQ                 -> open the window
  TikTokHQ VIDEO           -> open the window with VIDEO pre-selected
  TikTokHQ --classic       -> force the classic Tkinter window
  TikTokHQ --smoke FILE    -> no window; write "ok ..." to FILE if the bundled pieces work (CI)
  TikTokHQ --selftest-ui FILE [VIDEO]
                           -> open the modern window, drive it (and prepare VIDEO), write ok/FAIL to FILE (CI)
  TikTokHQ --cli ARGS...   -> run the command-line tool (needs a terminal)
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import tempfile
import threading
import traceback
from pathlib import Path
from typing import List, Optional

import tiktok_hq as core

APP_TITLE = f"TikTok HQ  v{core.VERSION}"
VIDEO_TYPES = [("Video", "*.mp4 *.mov *.m4v *.mkv *.webm *.avi *.mts *.ts *.MP4 *.MOV"), ("All files", "*.*")]

METHOD_CHOICES = [
    ("audio", "audio  -  الطريقة الافتراضية (الأحدث، ينصح بها)"),
    ("ghost", "ghost  -  تضخيم الفيديو (تيك توك يكشفها من أغسطس 2026)"),
    ("elst", "elst  -  تجريبية"),
    ("fps", "fps  -  تجريبية (مصدر 60 أو 120 إطار)"),
    ("all", "all  -  كل الطرق، ملف لكل طريقة (للمقارنة)"),
]
TAG_CHOICES = [
    ("replica", "replica  -  توقيع برنامج مونتاج (الافتراضي)"),
    ("plain", "plain  -  توقيع ffmpeg قديم"),
]

NEXT_STEPS = (
    "تم. الملف الجاهز بجانب الفيديو الأصلي (ينتهي بـ _TikTokHQ).\n\n"
    "الخطوات بعد كذا:\n"
    "1. ارفع من متصفح الكمبيوتر (tiktok.com/upload أو TikTok Studio)، مو من تطبيق الجوال.\n"
    "2. خلّ خيار الرفع بجودة عالية (HD) مفعّل.\n"
    "3. لا تسوي أي تعديل أو قص أو صوت داخل تيك توك.\n"
    "4. انشره أول شي بخصوصية (Only you)، وانتظر 5 دقايق.\n"
    "5. تأكد من الجودة من جهاز أو حساب ثاني، أو عن طريق بوت الفحص، ثم خلّه للكل."
)


# --------------------------------------------------------------------------- #
# helpers that do not need Tk
# --------------------------------------------------------------------------- #

def smoke(out_file: str) -> int:
    """Used by the build pipeline: prove the packaged app can find and run its ffmpeg/ffprobe."""
    lines: List[str] = []
    try:
        ffmpeg, ffprobe = core.require_tools()
        r = core.run([ffmpeg, "-version"], timeout=60)
        v = (r.stdout or "").splitlines()[0] if r.returncode == 0 and r.stdout else f"rc={r.returncode}"
        r2 = core.run([ffprobe, "-version"], timeout=60)
        v2 = (r2.stdout or "").splitlines()[0] if r2.returncode == 0 and r2.stdout else f"rc={r2.returncode}"
        ok = r.returncode == 0 and r2.returncode == 0
        try:
            import tkinter  # noqa: F401  (importing needs no display; a packaged app must have it)
            tk_note = f"tk={tkinter.TkVersion}"
        except Exception as e:  # noqa: BLE001
            tk_note = f"tk=MISSING ({e})"
            if core.is_frozen():
                ok = False
        # the modern UI: pywebview importable, its page files present, .NET bridge on Windows
        try:
            import webview  # noqa: F401
            from webview import _version as _wv
            ui_note = f"ui=webview:{_wv.__version__}"
        except Exception as e:  # noqa: BLE001
            ui_note = f"ui=MISSING ({e})"
            if core.is_frozen():
                ok = False
        try:
            import tiktok_hq_ui
            page = tiktok_hq_ui.ui_dir() / "index.html"
            ui_note += " files=ok" if page.is_file() else f" files=MISSING ({page})"
            if not page.is_file() and core.is_frozen():
                ok = False
        except Exception as e:  # noqa: BLE001
            ui_note += f" files=ERROR ({e})"
            if core.is_frozen():
                ok = False
        try:
            from webview.util import get_js_dir  # pywebview's injected JS must be inside the bundle
            ui_note += " js=ok" if (Path(get_js_dir()) / "api.js").is_file() else " js=MISSING"
        except Exception as e:  # noqa: BLE001
            ui_note += f" js=MISSING ({e})"
            if core.is_frozen():
                ok = False
        if os.name == "nt":
            try:
                import clr  # noqa: F401  (pythonnet: pywebview's WebView2 bridge)
                ui_note += " clr=ok"
            except Exception as e:  # noqa: BLE001
                ui_note += f" clr=MISSING ({e})"
                if core.is_frozen():
                    ok = False
            try:  # the backend webview.start() loads: WinForms + WebView2 interop DLLs
                import tiktok_hq_ui
                ui_note += f" platform=winforms renderer={tiktok_hq_ui.windows_renderer()}"
                import webview.platforms.edgechromium  # noqa: F401
                ui_note += " edgechromium=ok"
            except Exception as e:  # noqa: BLE001
                ui_note += f" platform=ERROR ({type(e).__name__}: {e})"
                if core.is_frozen():
                    ok = False
        elif sys.platform == "darwin":
            try:  # the backend webview.start() loads: AppKit + WebKit through pyobjc
                import webview.platforms.cocoa as cocoa
                ui_note += f" platform=cocoa renderer={getattr(cocoa, 'renderer', '?')}"
            except Exception as e:  # noqa: BLE001
                ui_note += f" platform=ERROR ({type(e).__name__}: {e})"
                if core.is_frozen():
                    ok = False
        lines.append(("ok" if ok else "FAIL") + f" version={core.VERSION} frozen={core.is_frozen()} {tk_note} {ui_note}")
        lines.append(f"ffmpeg={ffmpeg}")
        lines.append(f"ffprobe={ffprobe}")
        lines.append(v)
        lines.append(v2)
        rc = 0 if ok else 1
    except Exception as e:  # noqa: BLE001
        lines.append(f"FAIL {e}")
        rc = 1
    Path(out_file).write_text("\n".join(lines) + "\n", encoding="utf-8")
    return rc


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


# --------------------------------------------------------------------------- #
# the window
# --------------------------------------------------------------------------- #

def run_window(initial_file: Optional[str] = None, notice: Optional[List[str]] = None) -> int:
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    if os.name == "nt":  # crisp text on high-DPI screens
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass

    root = tk.Tk()
    root.title(APP_TITLE)
    root.minsize(760, 560)
    root.geometry("860x640")

    base_font = ("Segoe UI", 11) if os.name == "nt" else ("TkDefaultFont", 13)
    mono_font = ("Consolas", 10) if os.name == "nt" else ("Menlo", 11)
    style = ttk.Style(root)
    try:
        style.configure(".", font=base_font)
        style.configure("Big.TButton", font=(base_font[0], base_font[1] + 1, "bold"), padding=(14, 8))
    except Exception:
        pass

    q: "queue.Queue[tuple]" = queue.Queue()
    state = {"busy": False, "out_dir": None, "tools": None}

    # ----- widgets -------------------------------------------------------- #
    frm = ttk.Frame(root, padding=14)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(0, weight=1)

    ttk.Label(frm, text="TikTok HQ - جهّز الفيديو عشان تيك توك يعرضه بجودته الأصلية بدون إعادة ضغط",
              font=(base_font[0], base_font[1] + 2, "bold"), anchor="e", justify="right").grid(
        row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))

    file_var = tk.StringVar(value=initial_file or "")
    row = ttk.Frame(frm)
    row.grid(row=1, column=0, columnspan=2, sticky="ew")
    row.columnconfigure(0, weight=1)
    ent = ttk.Entry(row, textvariable=file_var, font=base_font)
    ent.grid(row=0, column=0, sticky="ew", padx=(0, 8))

    def choose_file() -> None:
        p = filedialog.askopenfilename(title="اختر الفيديو - Choose the video", filetypes=VIDEO_TYPES)
        if p:
            file_var.set(p)
            btn_open.config(state="disabled")

    ttk.Button(row, text="اختر فيديو  /  Choose video", command=choose_file).grid(row=0, column=1)

    opts = ttk.LabelFrame(frm, text="الإعدادات  /  Options", padding=10)
    opts.grid(row=2, column=0, columnspan=2, sticky="ew", pady=10)
    opts.columnconfigure(1, weight=1)

    method_var = tk.StringVar(value=METHOD_CHOICES[0][1])
    tags_var = tk.StringVar(value=TAG_CHOICES[0][1])
    force_var = tk.BooleanVar(value=False)
    outdir_var = tk.StringVar(value="")

    ttk.Label(opts, text="الطريقة / Method", anchor="e").grid(row=0, column=0, sticky="e", padx=(0, 8), pady=3)
    ttk.Combobox(opts, textvariable=method_var, values=[t for _, t in METHOD_CHOICES], state="readonly",
                 font=base_font).grid(row=0, column=1, sticky="ew", pady=3)
    ttk.Label(opts, text="التوقيع / Tags", anchor="e").grid(row=1, column=0, sticky="e", padx=(0, 8), pady=3)
    ttk.Combobox(opts, textvariable=tags_var, values=[t for _, t in TAG_CHOICES], state="readonly",
                 font=base_font).grid(row=1, column=1, sticky="ew", pady=3)
    ttk.Checkbutton(opts, text="إعادة ضغط إجبارية (استخدمها بس إذا تيك توك أعاد ضغط الملف)  /  Force re-encode",
                    variable=force_var).grid(row=2, column=0, columnspan=2, sticky="w", pady=3)

    outrow = ttk.Frame(opts)
    outrow.grid(row=3, column=0, columnspan=2, sticky="ew", pady=3)
    outrow.columnconfigure(1, weight=1)
    ttk.Label(outrow, text="مجلد الناتج / Output folder", anchor="e").grid(row=0, column=0, sticky="e", padx=(0, 8))
    ttk.Entry(outrow, textvariable=outdir_var, font=base_font).grid(row=0, column=1, sticky="ew", padx=(0, 8))

    def choose_dir() -> None:
        p = filedialog.askdirectory(title="مجلد الناتج - Output folder")
        if p:
            outdir_var.set(p)

    ttk.Button(outrow, text="اختر مجلد", command=choose_dir).grid(row=0, column=2)
    ttk.Label(outrow, text="(فاضي = بجانب الفيديو الأصلي)", anchor="w").grid(row=0, column=3, padx=(8, 0))

    btns = ttk.Frame(frm)
    btns.grid(row=3, column=0, columnspan=2, sticky="ew")
    btn_prep = ttk.Button(btns, text="جهّز الفيديو  /  Prepare", style="Big.TButton")
    btn_prep.pack(side="left")
    btn_check = ttk.Button(btns, text="فحص ملف  /  Check")
    btn_check.pack(side="left", padx=(10, 0))
    btn_compare = ttk.Button(btns, text="قارن المرفوع بالمعروض  /  Compare")
    btn_compare.pack(side="left", padx=(10, 0))
    btn_open = ttk.Button(btns, text="افتح مجلد الناتج  /  Open folder", state="disabled")
    btn_open.pack(side="right")

    status_var = tk.StringVar(value="")
    ttk.Label(frm, textvariable=status_var, anchor="w", font=mono_font).grid(
        row=4, column=0, columnspan=2, sticky="ew", pady=(10, 4))

    logf = ttk.Frame(frm)
    logf.grid(row=5, column=0, columnspan=2, sticky="nsew")
    frm.rowconfigure(5, weight=1)
    logf.columnconfigure(0, weight=1)
    logf.rowconfigure(0, weight=1)
    log = tk.Text(logf, wrap="none", font=mono_font, state="disabled", height=18)
    log.grid(row=0, column=0, sticky="nsew")
    sb = ttk.Scrollbar(logf, orient="vertical", command=log.yview)
    sb.grid(row=0, column=1, sticky="ns")
    log.configure(yscrollcommand=sb.set)
    hsb = ttk.Scrollbar(logf, orient="horizontal", command=log.xview)
    hsb.grid(row=1, column=0, sticky="ew")
    log.configure(xscrollcommand=hsb.set)

    # ----- plumbing ------------------------------------------------------- #
    def log_line(msg: str) -> None:
        log.configure(state="normal")
        log.insert("end", msg + "\n")
        log.see("end")
        log.configure(state="disabled")

    def set_busy(busy: bool) -> None:
        state["busy"] = busy
        for b in (btn_prep, btn_check, btn_compare):
            b.config(state="disabled" if busy else "normal")
        root.config(cursor="watch" if busy else "")

    def poll() -> None:
        try:
            while True:
                kind, payload = q.get_nowait()
                if kind == "line":
                    log_line(payload)
                elif kind == "progress":
                    status_var.set(payload)
                elif kind == "done":
                    set_busy(False)
                    rc, what = payload
                    status_var.set("")
                    if rc == 0 and what == "prep":
                        btn_open.config(state="normal")
                        messagebox.showinfo("TikTok HQ", NEXT_STEPS)
                    elif rc != 0 and what != "compare":
                        messagebox.showerror("TikTok HQ", "ما اكتمل. شوف آخر الأسطر في السجل تحت.\n\n" + str(what if isinstance(what, str) else ""))
        except queue.Empty:
            pass
        root.after(80, poll)

    def worker(argv: List[str], what: str) -> None:
        core.set_output(lambda m: q.put(("line", m)), lambda m: q.put(("progress", m)))
        rc = 1
        try:
            ns = core.build_parser().parse_args(argv)
            if ns.cmd == "check":
                rc = core.cmd_check(ns)
            elif ns.cmd == "compare":
                rc = core.cmd_compare(ns)
            else:
                rc = core.cmd_prep(ns)
        except core.ToolError as e:
            q.put(("line", f"\nERROR: {e}"))
            rc = 1
        except Exception:  # noqa: BLE001
            q.put(("line", "\nUNEXPECTED ERROR:\n" + traceback.format_exc()))
            rc = 1
        q.put(("done", (rc, what)))

    def start(argv: List[str], what: str) -> None:
        if state["busy"]:
            return
        log.configure(state="normal")
        log.delete("1.0", "end")
        log.configure(state="disabled")
        set_busy(True)
        btn_open.config(state="disabled")
        threading.Thread(target=worker, args=(argv, what), daemon=True).start()

    def current_file() -> Optional[Path]:
        p = file_var.get().strip().strip('"')
        if not p:
            messagebox.showwarning("TikTok HQ", "اختر فيديو أول.")
            return None
        src = Path(p).expanduser()
        if not src.is_file():
            messagebox.showwarning("TikTok HQ", f"الملف غير موجود:\n{src}")
            return None
        return src

    def on_prep() -> None:
        src = current_file()
        if not src:
            return
        method = next(k for k, t in METHOD_CHOICES if t == method_var.get())
        tags = next(k for k, t in TAG_CHOICES if t == tags_var.get())
        argv = ["prep", str(src), "--method", method, "--tags", tags]
        if force_var.get():
            argv.append("--force-encode")
        out_dir = outdir_var.get().strip()
        if out_dir:
            argv += ["--out-dir", out_dir]
        state["out_dir"] = Path(out_dir).expanduser() if out_dir else src.parent
        start(argv, "prep")

    def on_check() -> None:
        src = current_file()
        if src:
            start(["check", str(src)], "check")

    def on_compare() -> None:
        served = filedialog.askopenfilename(title="1) الملف اللي نزّلته من تيك توك - the SERVED file", filetypes=VIDEO_TYPES)
        if not served:
            return
        uploaded = filedialog.askopenfilename(title="2) الملف اللي رفعته (ناتج TikTok HQ) - the UPLOADED file", filetypes=VIDEO_TYPES)
        if not uploaded:
            return
        start(["compare", served, uploaded], "compare")

    def on_open() -> None:
        d = state["out_dir"]
        if d:
            open_folder(Path(d))

    btn_prep.config(command=on_prep)
    btn_check.config(command=on_check)
    btn_compare.config(command=on_compare)
    btn_open.config(command=on_open)

    # why the modern window is not showing (links are clickable)
    if notice:
        import webbrowser
        log.tag_configure("link", foreground="#1a6cf0", underline=True)
        log.tag_bind("link", "<Enter>", lambda e: log.configure(cursor="hand2"))
        log.tag_bind("link", "<Leave>", lambda e: log.configure(cursor=""))
        log.configure(state="normal")
        for i, n in enumerate(notice):
            if n.startswith("http"):
                tag = f"link{i}"
                log.insert("end", n, ("link", tag))
                log.tag_bind(tag, "<Button-1>", lambda e, url=n: webbrowser.open(url))
                log.insert("end", "\n")
            else:
                log.insert("end", n + "\n")
        log.configure(state="disabled")

    # tools check at startup (cheap)
    try:
        ffmpeg, ffprobe = core.require_tools()
        state["tools"] = (ffmpeg, ffprobe)
        log_line(f"TikTok HQ v{core.VERSION}")
        log_line(f"ffmpeg  : {ffmpeg}")
        log_line(f"ffprobe : {ffprobe}")
        log_line("")
        log_line("اختر فيديو ثم اضغط \"جهّز الفيديو\".  /  Choose a video, then press Prepare.")
    except core.ToolError as e:
        log_line(f"ERROR: {e}")
        messagebox.showerror("TikTok HQ", "ffmpeg غير موجود داخل البرنامج.\n\n" + str(e))

    root.after(80, poll)
    root.mainloop()
    return 0


def run_modern(initial_file: Optional[str]) -> int:
    """The web-view window; raises if it cannot start so the caller can fall back."""
    import tiktok_hq_ui
    return tiktok_hq_ui.run(initial_file)


def classic_notice(exc: BaseException, log: Optional[Path]) -> List[str]:
    """Lines shown at the top of the classic window when the modern one could not start."""
    if getattr(exc, "reason", "") == "webview2":
        import tiktok_hq_ui
        return [
            "تنبيه: الواجهة الحديثة تحتاج مكوّن Microsoft Edge WebView2 وهو غير موجود على هذا الجهاز،",
            "فتحنا لك الواجهة الكلاسيكية (نفس المحرك ونفس النتيجة). للواجهة الحديثة ثبّت WebView2 Runtime من:",
            tiktok_hq_ui.WEBVIEW2_URL,
            "Note: the modern window needs the Microsoft Edge WebView2 Runtime (link above); using the classic window.",
            "",
        ]
    lines = [
        "تنبيه: الواجهة الحديثة ما اشتغلت على هذا الجهاز، فتحنا لك الواجهة الكلاسيكية (نفس المحرك ونفس النتيجة).",
        "Note: the modern window could not start on this machine; using the classic window.",
    ]
    if log:
        lines.append(f"log: {log}")
    return lines + [""]


def selftest_ui(argv: List[str]) -> int:
    """CI: open the real modern window, drive it, write ok/FAIL to a file. Never falls back."""
    out = str(Path(argv[0] if argv else "tiktokhq-ui-selftest.txt").resolve())
    initial = str(Path(argv[1]).resolve()) if len(argv) > 1 else None
    try:
        import tiktok_hq_ui
        return tiktok_hq_ui.run(initial, selftest=out)
    except Exception as e:  # noqa: BLE001
        Path(out).write_text(f"FAIL version={core.VERSION} platform={sys.platform}\nerror={e!r}\n"
                             f"{traceback.format_exc()}\ndone\n", encoding="utf-8")
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv and argv[0] == "--smoke":
        return smoke(argv[1] if len(argv) > 1 else "tiktokhq-smoke.txt")
    if argv and argv[0] == "--cli":
        return core.main(argv[1:])
    if argv and argv[0] == "--selftest-ui":
        return selftest_ui(argv[1:])
    classic = "--classic" in argv
    argv = [a for a in argv if a != "--classic"]
    initial = argv[0] if argv and not argv[0].startswith("-") else None
    notice: Optional[List[str]] = None
    if not classic:
        try:
            return run_modern(initial)
        except Exception as e:  # noqa: BLE001  (no pywebview / no WebView2 / page never loaded)
            log: Optional[Path] = None
            try:
                log = Path(tempfile.gettempdir()) / "tiktokhq-ui-fallback.log"
                log.write_text(traceback.format_exc(), encoding="utf-8")
            except Exception:
                log = None
            notice = classic_notice(e, log)
    return run_window(initial, notice=notice)


if __name__ == "__main__":
    sys.exit(main())
