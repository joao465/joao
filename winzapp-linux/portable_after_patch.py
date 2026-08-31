from __future__ import annotations

import shutil
import sys
from pathlib import Path


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: portable_after_patch.py <WinZapp source root> <portable diagnostics template>", file=sys.stderr)
        return 2

    root = Path(sys.argv[1]).resolve()
    diagnostics_template = Path(sys.argv[2]).resolve()
    client = root / "client"
    app_paths = client / "app_paths.py"

    text = app_paths.read_text(encoding="utf-8")
    old = '''    if sys.platform != "win32":\n        xdg = os.environ.get("XDG_DATA_HOME")\n        if not xdg:\n            xdg = os.path.join(os.path.expanduser("~"), ".local", "share")\n        target = os.path.join(xdg, "winzapp")\n        os.makedirs(target, exist_ok=True)\n        return target\n'''
    new = '''    if sys.platform != "win32":\n        # Portable Linux build: keep writable application state beside the executable.\n        if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):\n            return os.path.dirname(os.path.abspath(sys.executable))\n        return os.getcwd()\n'''
    if old not in text:
        raise SystemExit("portable writable path block not found in app_paths.py")
    app_paths.write_text(text.replace(old, new, 1), encoding="utf-8")

    target_diag = client / "linux_diagnostics.py"
    shutil.copyfile(diagnostics_template, target_diag)

    compile(app_paths.read_text(encoding="utf-8"), str(app_paths), "exec")
    compile(target_diag.read_text(encoding="utf-8"), str(target_diag), "exec")
    print("Portable Linux paths enabled: data/logs resolve beside executable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
