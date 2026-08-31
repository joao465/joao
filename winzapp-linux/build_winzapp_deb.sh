#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="${GITHUB_WORKSPACE:-$PWD}"
SRC="$ROOT/winzapp-src"
PATCHER="$ROOT/winzapp-linux/phase1.py"
BUILD="$ROOT/winzapp-build"
VENV="$BUILD/venv"
DIST="$BUILD/dist"
APPDIR="$DIST/WinZapp"
PKGROOT="$BUILD/pkg"
PUPPETEER_CACHE="$BUILD/puppeteer-cache"
OUT="$ROOT/out"
step(){ printf '\n===== %s =====\n' "$*"; }
rm -rf "$SRC" "$BUILD" "$OUT"
mkdir -p "$BUILD" "$OUT"

step "Clone WinZapp source"
git clone --depth 1 https://github.com/JoaoDEVWHADS/WinZapp_Python.git "$SRC"
cd "$SRC"
printf 'source_commit=%s\n' "$(git rev-parse HEAD)" | tee "$BUILD/source-info.txt"

step "Apply Linux portability patch"
python3 "$PATCHER" "$SRC"

step "Apply frozen Linux paths, diagnostics and build guards"
python3 - "$SRC" <<'PY'
from pathlib import Path
import re, sys
root = Path(sys.argv[1])
req = root / "requirements.txt"
lines=[]
for line in req.read_text(encoding="utf-8").splitlines():
    s=line.strip()
    if not s or s.startswith('#'):
        lines.append(line); continue
    pkg=re.split(r"[<>=!~;\s]", s, maxsplit=1)[0].lower()
    if pkg in {"wxpython", "pyaudio"}:
        continue
    if pkg == "audioop-lts":
        line = line.split(';',1)[0] + '; python_version >= "3.13"'
    lines.append(line)
req.write_text("\n".join(lines)+"\n", encoding="utf-8")

app_paths = root / "client" / "app_paths.py"
text = app_paths.read_text(encoding="utf-8")
old = '''def _writable_base_dir() -> str:\n    """Return the directory external writable data/logs live under when frozen."""\n    if hasattr(sys, "_MEIPASS"):\n        return os.path.dirname(sys.executable)\n    if sys.argv and sys.argv[0]:\n        return os.path.dirname(os.path.abspath(sys.argv[0]))\n    return os.path.dirname(sys.executable)\n'''
new = '''def _writable_base_dir() -> str:\n    """Return a writable per-user base directory on Linux."""\n    if sys.platform != "win32":\n        xdg = os.environ.get("XDG_DATA_HOME")\n        if not xdg:\n            xdg = os.path.join(os.path.expanduser("~"), ".local", "share")\n        target = os.path.join(xdg, "winzapp")\n        os.makedirs(target, exist_ok=True)\n        return target\n    if hasattr(sys, "_MEIPASS"):\n        return os.path.dirname(sys.executable)\n    if sys.argv and sys.argv[0]:\n        return os.path.dirname(os.path.abspath(sys.argv[0]))\n    return os.path.dirname(sys.executable)\n'''
if old not in text:
    raise SystemExit("app_paths.py writable block not found")
app_paths.write_text(text.replace(old,new,1), encoding="utf-8")

diag = root / "client" / "linux_diagnostics.py"
diag.write_text(r'''from __future__ import annotations
import atexit, datetime, faulthandler, logging, os, platform, sys, threading, traceback
_STREAM=None
def _ts(): return datetime.datetime.now().astimezone().isoformat(timespec="seconds")
def _w(msg):
    if _STREAM:
        try:
            _STREAM.write(msg + ("" if msg.endswith("\n") else "\n")); _STREAM.flush()
        except Exception: pass
def install_linux_diagnostics():
    global _STREAM
    if sys.platform == "win32": return
    logdir=os.environ.get("WINZAPP_DIAGNOSTIC_LOG_DIR") or os.path.join(os.path.expanduser("~"),"WinZappLogs")
    os.makedirs(logdir, exist_ok=True)
    _STREAM=open(os.path.join(logdir,"crash.log"),"a",encoding="utf-8",buffering=1)
    _w("="*78); _w(f"[{_ts()}] process start pid={os.getpid()} ppid={os.getppid()}")
    _w(f"python={sys.version.replace(chr(10),' ')}"); _w(f"platform={platform.platform()}")
    _w(f"argv={sys.argv!r}"); _w(f"cwd={os.getcwd()}")
    try: faulthandler.enable(file=_STREAM, all_threads=True)
    except Exception: _w(traceback.format_exc())
    h=logging.FileHandler(os.path.join(logdir,"python.log"),encoding="utf-8")
    h.setLevel(logging.DEBUG); h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"))
    root=logging.getLogger(); root.addHandler(h)
    if root.level > logging.DEBUG: root.setLevel(logging.DEBUG)
    old=sys.excepthook
    def mh(t,v,tb):
        _w(f"[{_ts()}] UNHANDLED MAIN EXCEPTION"); traceback.print_exception(t,v,tb,file=_STREAM)
        try: old(t,v,tb)
        except Exception: pass
    sys.excepthook=mh
    if hasattr(threading,"excepthook"):
        oldt=threading.excepthook
        def th(args):
            _w(f"[{_ts()}] UNHANDLED THREAD {getattr(args.thread,'name',None)!r}")
            traceback.print_exception(args.exc_type,args.exc_value,args.exc_traceback,file=_STREAM)
            try: oldt(args)
            except Exception: pass
        threading.excepthook=th
    atexit.register(lambda: _w(f"[{_ts()}] Python atexit reached"))
''', encoding="utf-8")
main = root / "client" / "main.py"
m = main.read_text(encoding="utf-8")
if "# WINZAPP_PACKAGED_LINUX_DIAGNOSTICS" not in m:
    anchor="import time\n"
    block='''import time\n\n# WINZAPP_PACKAGED_LINUX_DIAGNOSTICS\nif sys.platform != "win32":\n    try:\n        from linux_diagnostics import install_linux_diagnostics\n        install_linux_diagnostics()\n    except Exception:\n        import traceback as _wz_diag_tb\n        _wz_diag_tb.print_exc()\n'''
    if anchor not in m: raise SystemExit("main.py import time anchor not found")
    main.write_text(m.replace(anchor,block,1), encoding="utf-8")
PY

step "Create build venv using distro wxGTK and PyAudio"
python3 -m venv --system-site-packages "$VENV"
"$VENV/bin/python" -m pip install --upgrade pip setuptools wheel pyinstaller
"$VENV/bin/python" -m pip install -r "$SRC/requirements.txt"

step "Build WPPConnect and download Puppeteer browser"
export PUPPETEER_CACHE_DIR="$PUPPETEER_CACHE"
export PUPPETEER_SKIP_DOWNLOAD=false
cd "$SRC"
"$VENV/bin/python" setup_api.py --clean
test -f "$SRC/client/api/dist/server.js"
test -d "$SRC/client/api/node_modules"

step "PyInstaller Linux onedir"
cd "$SRC"
rm -rf "$DIST" "$BUILD/pyi-work" "$BUILD/pyi-spec"
mkdir -p "$DIST" "$BUILD/pyi-work" "$BUILD/pyi-spec"
"$VENV/bin/python" -m PyInstaller \
  --noconfirm --clean --onedir --console --name WinZapp \
  --distpath "$DIST" --workpath "$BUILD/pyi-work" --specpath "$BUILD/pyi-spec" \
  --paths "$SRC/client" \
  --hidden-import wx.adv --hidden-import socketio --hidden-import engineio --hidden-import pyaudio \
  --collect-all wx --collect-all sound_lib --collect-all accessible_output2 \
  --collect-all platform_utils --collect-all libloader --collect-all cryptography \
  --collect-all requests --collect-all socketio --collect-all engineio \
  --collect-all pyperclip --collect-all aiosqlite --collect-all numpy \
  --collect-all sounddevice --collect-all soundfile \
  "$SRC/client/main.py"
test -x "$APPDIR/WinZapp"

step "Copy runtime assets, WPPConnect, Node and Chromium"
for d in sounds languages data api_patches; do
  if [[ -d "$SRC/client/$d" ]]; then rm -rf "$APPDIR/$d"; cp -a "$SRC/client/$d" "$APPDIR/$d"; fi
done
rm -rf "$APPDIR/api"
cp -a "$SRC/client/api" "$APPDIR/api"
rm -rf "$APPDIR/api/.git" "$APPDIR/api/.github" "$APPDIR/api/coverage" "$APPDIR/api/tests" 2>/dev/null || true
find "$APPDIR/api" -type d -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
mkdir -p "$APPDIR/node"
cp -L "$(command -v node)" "$APPDIR/node/node"
chmod 0755 "$APPDIR/node/node"
if [[ -d "$PUPPETEER_CACHE" ]]; then rm -rf "$APPDIR/puppeteer-cache"; cp -a "$PUPPETEER_CACHE" "$APPDIR/puppeteer-cache"; fi
for f in "$SRC/.env" "$SRC/client/.env" "$SRC/.env.example"; do if [[ -f "$f" ]]; then cp -a "$f" "$APPDIR/.env"; break; fi; done

step "Check executable shared libraries"
ldd "$APPDIR/WinZapp" | tee "$BUILD/ldd.txt"
if grep -q 'not found' "$BUILD/ldd.txt"; then echo "Unresolved shared libraries" >&2; exit 1; fi

step "Assemble Debian package"
VERSION=$(python3 - "$SRC/client/version.py" <<'PY'
import re,sys
s=open(sys.argv[1],encoding='utf-8').read(); m=re.search(r'__version__\s*=\s*["\']([^"\']+)',s)
v=m.group(1) if m else '0.0.0'; print(re.sub(r'[^0-9A-Za-z.+:~_-]','.',v) + '-linux1')
PY
)
ARCH=amd64
rm -rf "$PKGROOT"
mkdir -p "$PKGROOT/DEBIAN" "$PKGROOT/opt/winzapp" "$PKGROOT/usr/bin" "$PKGROOT/usr/share/applications"
cp -a "$APPDIR/." "$PKGROOT/opt/winzapp/"
cat > "$PKGROOT/usr/bin/winzapp" <<'LAUNCH'
#!/usr/bin/env bash
set +e
APP=/opt/winzapp
LOGDIR="$HOME/WinZappLogs"
mkdir -p "$LOGDIR"; chmod 700 "$LOGDIR" 2>/dev/null || true
STAMP="$(date '+%Y%m%d-%H%M%S')"; SESSION="$LOGDIR/session-$STAMP.log"; PROCS="$LOGDIR/processes-$STAMP.log"
export WINZAPP_DIAGNOSTIC_LOG_DIR="$LOGDIR" PYTHONFAULTHANDLER=1 PYTHONUNBUFFERED=1
export PUPPETEER_CACHE_DIR="$APP/puppeteer-cache" PATH="$APP/node:$PATH"
export XDG_DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
{
 echo "============================================================"; echo "WinZapp packaged Linux session"; echo "date=$(date --iso-8601=seconds 2>/dev/null || date)"
 echo "user=$(id -un) uid=$(id -u)"; echo "home=$HOME"; echo "DISPLAY=${DISPLAY:-}"; echo "WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-}"; echo "XDG_SESSION_TYPE=${XDG_SESSION_TYPE:-}"
 echo "node=$($APP/node/node --version 2>&1)"; echo "============================================================"
} >> "$SESSION"
(
 while true; do
  echo "--- $(date --iso-8601=seconds 2>/dev/null || date) ---"
  ps -eo pid,ppid,stat,etime,cmd | grep -E '[W]inZapp|[n]ode|[c]hrom(e|ium)' || true
  (command -v ss >/dev/null && ss -ltnp 2>/dev/null | grep ':6300' || true)
  sleep 2
 done
) >> "$PROCS" 2>&1 & MON=$!
cd "$APP" || exit 125
stdbuf -oL -eL "$APP/WinZapp" "$@" >> "$SESSION" 2>&1
RC=$?
kill "$MON" 2>/dev/null || true; wait "$MON" 2>/dev/null || true
{ echo "exit_code=$RC"; echo "finished=$(date --iso-8601=seconds 2>/dev/null || date)"; } >> "$SESSION"
ln -sfn "$SESSION" "$LOGDIR/latest-session.log"; ln -sfn "$PROCS" "$LOGDIR/latest-processes.log"
DATA_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/winzapp"
if [[ -d "$DATA_ROOT" ]]; then
 REC="$LOGDIR/internal-$STAMP"; mkdir -p "$REC"
 find "$DATA_ROOT" -type f \( -name '*.log' -o -name 'crash.log' \) -print0 2>/dev/null | while IFS= read -r -d '' f; do rel="${f#$DATA_ROOT/}"; safe="${rel//\//__}"; cp -f "$f" "$REC/$safe" 2>/dev/null || true; done
fi
exit "$RC"
LAUNCH
chmod 0755 "$PKGROOT/usr/bin/winzapp"
cat > "$PKGROOT/usr/bin/winzapp-logs" <<'LOGS'
#!/usr/bin/env bash
set -e
D="$HOME/WinZappLogs"; mkdir -p "$D"; echo "Pasta: $D"; echo
find "$D" -maxdepth 2 -type f -printf '%TY-%Tm-%Td %TH:%TM:%TS %p\n' 2>/dev/null | sort -r | head -40 || true
echo
[[ -e "$D/latest-session.log" ]] && { echo "===== latest-session.log ====="; tail -n 220 "$D/latest-session.log"; }
[[ -e "$D/crash.log" ]] && { echo; echo "===== crash.log ====="; tail -n 220 "$D/crash.log"; }
[[ -e "$D/python.log" ]] && { echo; echo "===== python.log ====="; tail -n 160 "$D/python.log"; }
[[ -e "$D/latest-processes.log" ]] && { echo; echo "===== latest-processes.log ====="; tail -n 160 "$D/latest-processes.log"; }
LOGS
chmod 0755 "$PKGROOT/usr/bin/winzapp-logs"
cat > "$PKGROOT/usr/share/applications/winzapp.desktop" <<'DESKTOP'
[Desktop Entry]
Type=Application
Name=WinZapp
Comment=Cliente acessível do WhatsApp
Exec=/usr/bin/winzapp
Icon=applications-internet
Terminal=false
Categories=Network;InstantMessaging;
StartupNotify=true
DESKTOP
cat > "$PKGROOT/DEBIAN/control" <<EOF
Package: winzapp
Version: $VERSION
Section: net
Priority: optional
Architecture: $ARCH
Maintainer: WinZapp Linux build
Depends: libc6, libstdc++6, libglib2.0-0t64 | libglib2.0-0, libgtk-3-0t64 | libgtk-3-0, libasound2t64 | libasound2, libportaudio2, libsndfile1, libnss3, libgbm1, libx11-6, libxss1, libxkbcommon0, libxcomposite1, libxdamage1, libxrandr2, libdrm2
Description: WinZapp for Linux with WPPConnect and diagnostics
 Contains the WinZapp client, compiled WPPConnect backend, bundled Node.js,
 Puppeteer browser cache and persistent logs in ~/WinZappLogs.
EOF
cat > "$PKGROOT/DEBIAN/postinst" <<'POST'
#!/bin/sh
set -e
chmod 0755 /opt/winzapp/WinZapp /opt/winzapp/node/node /usr/bin/winzapp /usr/bin/winzapp-logs 2>/dev/null || true
exit 0
POST
chmod 0755 "$PKGROOT/DEBIAN/postinst"
find "$PKGROOT" -type d -exec chmod 0755 {} +
chmod 0755 "$PKGROOT/DEBIAN" "$PKGROOT/DEBIAN/postinst"
chmod 0644 "$PKGROOT/DEBIAN/control" "$PKGROOT/usr/share/applications/winzapp.desktop"
DEB="$OUT/winzapp_${VERSION}_${ARCH}.deb"
dpkg-deb -Zxz -z6 --root-owner-group --build "$PKGROOT" "$DEB"
dpkg-deb -I "$DEB"
sha256sum "$DEB" | tee "$DEB.sha256"
ls -lh "$DEB"
