#!/usr/bin/env python3
"""Apply the first Linux portability pass to WinZapp_Python.

This script is intentionally conservative:
- it keeps the Windows behavior in place;
- it only adds platform guards/backends where Linux currently crashes;
- it creates backups in .linux-port-backup-phase1 before replacing files;
- it is safe to run again (already-applied edits are detected).

Run from the repository root, or pass the repository path as the first argument.
"""
from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(sys.argv[1] if len(sys.argv) > 1 else ".").resolve()
BACKUP = ROOT / ".linux-port-backup-phase1"


def fail(message: str) -> None:
    raise SystemExit(f"[ERRO] {message}")


def require(path: str) -> Path:
    p = ROOT / path
    if not p.exists():
        fail(f"Arquivo esperado não encontrado: {p}")
    return p


def backup(path: Path) -> None:
    rel = path.relative_to(ROOT)
    dst = BACKUP / rel
    if dst.exists():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(path, dst)


def write(path: Path, text: str) -> None:
    backup(path)
    path.write_text(text, encoding="utf-8", newline="\n")
    print(f"[OK] {path.relative_to(ROOT)}")


for expected in ("requirements.txt", "client/main.py", "client/autostart.py", "client/core/notification_manager.py"):
    require(expected)

# 1) Windows-only Python packages must not be installed on Linux.
req_path = ROOT / "requirements.txt"
req = req_path.read_text(encoding="utf-8")
windows_only = {
    "pywin32": 'sys_platform == "win32"',
    "pywin32-ctypes": 'sys_platform == "win32"',
    "Windows-Toasts": 'sys_platform == "win32"',
    "winrt-runtime": 'sys_platform == "win32"',
    "winrt-Windows.Data.Xml.Dom": 'sys_platform == "win32"',
    "winrt-Windows.Foundation": 'sys_platform == "win32"',
    "winrt-Windows.Foundation.Collections": 'sys_platform == "win32"',
    "winrt-Windows.UI.Notifications": 'sys_platform == "win32"',
}
req_lines = []
changed_req = False
for line in req.splitlines():
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        req_lines.append(line)
        continue
    package = re.split(r"[<>=!~;\s]", stripped, maxsplit=1)[0]
    marker = windows_only.get(package)
    if marker and ";" not in line:
        line = f"{line}; {marker}"
        changed_req = True
    req_lines.append(line)
if changed_req:
    write(req_path, "\n".join(req_lines) + "\n")
else:
    print("[OK] requirements.txt já possui os marcadores necessários")

# 2) Cross-platform autostart + per-account single-instance lock.
autostart_path = ROOT / "client" / "autostart.py"
autostart_new = r'''"""Cross-platform autostart and single-instance helpers for WinZapp.

Windows:
  HKCU\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Run
  named mutex (same behavior as the original implementation)

Linux/Unix:
  XDG autostart desktop entry (~/.config/autostart/winzapp.desktop by default)
  advisory fcntl.flock lock in the active account data directory
"""

from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path

from app_paths import data_path

_AUTORUN_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\Run"
_AUTORUN_NAME = "WinZapp"
_mutex_handle = None


def _launch_argv() -> list[str]:
    argv0 = os.path.abspath(sys.argv[0])
    if getattr(sys, "frozen", False) or argv0.lower().endswith(".exe"):
        return [argv0, "--background"]
    return [sys.executable, argv0, "--background"]


def get_autostart_command() -> str:
    argv = _launch_argv()
    if sys.platform == "win32":
        return subprocess.list2cmdline(argv)
    return " ".join(_desktop_quote(arg) for arg in argv)


def _desktop_quote(value: str) -> str:
    if value and not any(ch.isspace() or ch in '\\"`$' for ch in value):
        return value
    escaped = (value.replace("\\", "\\\\")
                    .replace('"', '\\"')
                    .replace('`', '\\`')
                    .replace('$', '\\$'))
    return f'"{escaped}"'


def _linux_autostart_path() -> Path:
    config_home = os.environ.get("XDG_CONFIG_HOME")
    if not config_home:
        config_home = os.path.join(os.path.expanduser("~"), ".config")
    return Path(config_home) / "autostart" / "winzapp.desktop"


def is_autostart_enabled() -> bool:
    if sys.platform != "win32":
        return _linux_autostart_path().is_file()
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTORUN_KEY, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, _AUTORUN_NAME)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def enable_autostart() -> None:
    if sys.platform != "win32":
        target = _linux_autostart_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        content = (
            "[Desktop Entry]\n"
            "Type=Application\n"
            "Name=WinZapp\n"
            "Comment=Iniciar o WinZapp com a sessão gráfica\n"
            f"Exec={get_autostart_command()}\n"
            "Terminal=false\n"
            "X-GNOME-Autostart-enabled=true\n"
        )
        target.write_text(content, encoding="utf-8")
        try:
            target.chmod(0o644)
        except OSError:
            pass
        return
    import winreg
    cmd = get_autostart_command()
    key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTORUN_KEY, 0, winreg.KEY_SET_VALUE)
    winreg.SetValueEx(key, _AUTORUN_NAME, 0, winreg.REG_SZ, cmd)
    winreg.CloseKey(key)


def disable_autostart() -> None:
    if sys.platform != "win32":
        try:
            _linux_autostart_path().unlink()
        except FileNotFoundError:
            pass
        return
    import winreg
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, _AUTORUN_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, _AUTORUN_NAME)
        winreg.CloseKey(key)
    except OSError:
        pass


def _get_dynamic_mutex_name() -> str:
    try:
        path_hash = hashlib.md5(data_path().encode("utf-8")).hexdigest()
        return f"Global\\WinZappSingleInstance_{path_hash}"
    except Exception:
        return "Global\\WinZappSingleInstance"


def acquire_single_instance_mutex() -> bool:
    global _mutex_handle
    if sys.platform == "win32":
        import ctypes
        mutex_name = _get_dynamic_mutex_name()
        _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, True, mutex_name)
        return ctypes.windll.kernel32.GetLastError() != 183
    import fcntl
    os.makedirs(data_path(), exist_ok=True)
    lock_path = os.path.join(data_path(), ".winzapp-instance.lock")
    handle = open(lock_path, "a+")
    try:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (BlockingIOError, OSError):
        handle.close()
        return False
    try:
        os.chmod(lock_path, 0o600)
    except OSError:
        pass
    _mutex_handle = handle
    return True


def activate_existing_window() -> None:
    if sys.platform != "win32":
        return
    import ctypes
    from ctypes import wintypes
    found = [0]
    @ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)
    def _enum_proc(hwnd, lparam):
        buf = ctypes.create_unicode_buffer(256)
        ctypes.windll.user32.GetWindowTextW(hwnd, buf, 256)
        if buf.value.startswith("WinZapp"):
            found[0] = hwnd
            return False
        return True
    ctypes.windll.user32.EnumWindows(_enum_proc, 0)
    if found[0]:
        ctypes.windll.user32.ShowWindow(found[0], 5)
        ctypes.windll.user32.SetForegroundWindow(found[0])
'''
current_autostart = autostart_path.read_text(encoding="utf-8")
if "XDG autostart desktop entry" not in current_autostart:
    write(autostart_path, autostart_new)
else:
    print("[OK] client/autostart.py já está portado")

# 3) NotificationManager: keep Windows WinRT toasts, use wx native NotificationMessage on Linux.
notif_path = ROOT / "client" / "core" / "notification_manager.py"
notif = notif_path.read_text(encoding="utf-8")
notif_changed = False
init_sig = "    def __init__(self, main_window):\n"
linux_init_marker = "self._linux_native_notifications = True"
if linux_init_marker not in notif:
    idx = notif.find(init_sig, notif.find("class NotificationManager:"))
    if idx < 0:
        fail("Não consegui localizar NotificationManager.__init__")
    insert_at = idx + len(init_sig)
    linux_init = '''        if sys.platform != "win32":\n            self.main_window = main_window\n            self.i18n = I18n(main_window)\n            self.i18n.get_language()\n            self._linux_native_notifications = True\n            self._linux_notification = None\n            return\n        self._linux_native_notifications = False\n'''
    notif = notif[:insert_at] + linux_init + notif[insert_at:]
    notif_changed = True
send_sig = "    def send(self, title: str, body: str, remote_jid: str, msg_key: dict = None):\n"
linux_send_marker = "_send_linux_notification"
if linux_send_marker not in notif:
    idx = notif.find(send_sig, notif.find("class NotificationManager:"))
    if idx < 0:
        fail("Não consegui localizar NotificationManager.send")
    body_start = idx + len(send_sig)
    first_triple = notif.find('        """', body_start)
    if first_triple < 0:
        fail("Docstring de NotificationManager.send não localizada")
    doc_end = notif.find('        """', first_triple + 11)
    if doc_end < 0:
        fail("Fim da docstring de NotificationManager.send não localizado")
    doc_end = notif.find("\n", doc_end + 11) + 1
    linux_send = '''        if getattr(self, "_linux_native_notifications", False):\n            wx.CallAfter(self._send_linux_notification, title, body, remote_jid)\n            return\n'''
    notif = notif[:doc_end] + linux_send + notif[doc_end:]
    method_insert = notif.find("    # ── Callbacks", doc_end)
    if method_insert < 0:
        fail("Ponto de inserção do backend Linux de notificações não localizado")
    linux_method = r'''    def _send_linux_notification(self, title: str, body: str, remote_jid: str = ""):
        from core.quiet_hours import is_quiet_hours_active
        if not is_quiet_hours_active():
            try:
                self._play_sound(remote_jid)
            except Exception:
                pass
        try:
            import wx.adv
            note = wx.adv.NotificationMessage(title, body)
            shown = note.Show()
            self._linux_notification = note
            if shown is False:
                self._announce_unshown(title, body)
        except Exception as exc:
            logging.warning("[NotificationManager] Linux notification failed: %s", exc)
            self._announce_unshown(title, body)

'''
    notif = notif[:method_insert] + linux_method + notif[method_insert:]
    notif_changed = True
if notif_changed:
    write(notif_path, notif)
else:
    print("[OK] client/core/notification_manager.py já possui backend Linux")

# 4) main.py: guard Win32-only hotkey/tray/restore/startup message blocks.
main_path = ROOT / "client" / "main.py"
main = main_path.read_text(encoding="utf-8")
main_changed = False
hotkey_anchor = '''    def _apply_global_hotkey(self):\n        """Register (or unregister) the global hotkey from settings."""\n'''
if "Global hotkey is Windows-only in phase 1" not in main:
    if hotkey_anchor not in main:
        fail("Não consegui localizar _apply_global_hotkey")
    repl = hotkey_anchor + '''        if sys.platform != "win32":\n            # Global hotkey is Windows-only in phase 1.\n            self._hotkey_manager = None\n            return\n'''
    main = main.replace(hotkey_anchor, repl, 1)
    main_changed = True
tray_old = '''    def _init_tray(self):\n        """Create the system-tray icon if the setting is enabled."""\n        show = self.settings.get("general", {}).get("show_tray_icon", True)\n        if show:\n            from core.tray_manager import TrayIcon\n            self.tray_icon = TrayIcon(self)\n'''
if "system tray unavailable on this desktop" not in main:
    if tray_old not in main:
        fail("Não consegui localizar _init_tray")
    tray_new = '''    def _init_tray(self):\n        """Create the system-tray icon if the setting is enabled."""\n        show = self.settings.get("general", {}).get("show_tray_icon", True)\n        if show:\n            try:\n                from core.tray_manager import TrayIcon\n                self.tray_icon = TrayIcon(self)\n            except Exception as exc:\n                logging.warning("[tray] system tray unavailable on this desktop: %s", exc)\n                self.tray_icon = None\n'''
    main = main.replace(tray_old, tray_new, 1)
    main_changed = True
if "Linux/Unix: wx owns the window state directly" not in main:
    restore_start = main.find("    def restore_window(self):")
    if restore_start < 0:
        fail("Não consegui localizar restore_window")
    import_pos = main.find("        import ctypes\n", restore_start)
    hidden_pos = main.find("        self._window_hidden = False\n", import_pos)
    if import_pos < 0 or hidden_pos < 0 or hidden_pos - import_pos > 8000:
        fail("Bloco Win32 de restore_window não localizado com segurança")
    hidden_end = hidden_pos + len("        self._window_hidden = False\n")
    restore_platform = '''        if sys.platform == "win32":\n            import ctypes\n            hwnd = self.GetHandle()\n            SW_SHOWMAXIMIZED = 3\n            user32 = ctypes.windll.user32\n            user32.ShowWindow(hwnd, SW_SHOWMAXIMIZED)\n            try:\n                kernel32 = ctypes.windll.kernel32\n                fg_hwnd = user32.GetForegroundWindow()\n                fg_thread = user32.GetWindowThreadProcessId(fg_hwnd, None) if fg_hwnd else 0\n                cur_thread = kernel32.GetCurrentThreadId()\n                attached = False\n                if fg_thread and fg_thread != cur_thread:\n                    attached = bool(user32.AttachThreadInput(fg_thread, cur_thread, True))\n                user32.BringWindowToTop(hwnd)\n                user32.SetForegroundWindow(hwnd)\n                user32.SetActiveWindow(hwnd)\n                if attached:\n                    user32.AttachThreadInput(fg_thread, cur_thread, False)\n            except Exception:\n                user32.SetForegroundWindow(hwnd)\n        else:\n            # Linux/Unix: wx owns the window state directly.\n            if not self.IsShown():\n                self.Show(True)\n            if self.IsIconized():\n                self.Iconize(False)\n            try:\n                self.Maximize(True)\n            except Exception:\n                pass\n            try:\n                self.Raise()\n            except Exception:\n                pass\n        self._window_hidden = False\n'''
    main = main[:import_pos] + restore_platform + main[hidden_end:]
    main_changed = True
error_old = '''        if _mode == "error":\n            ctypes.windll.user32.MessageBoxW(\n                0, _startup_t("startup_invalid_account"),\n                "WinZapp", 0x10)\n            sys.exit(2)\n        elif _mode == "manager":\n            # Global manager mode: no account/data_path, no Node (plan sekcja F).\n            # TODO(Zad 4.5/4.6): show the account manager. For now, inform+exit.\n            ctypes.windll.user32.MessageBoxW(\n                0, _startup_t("startup_account_manager_unavailable"),\n                "WinZapp", 0x40)\n            sys.exit(0)\n'''
if "startup message (Linux)" not in main and error_old in main:
    error_new = '''        if _mode == "error":\n            _msg = _startup_t("startup_invalid_account")\n            if sys.platform == "win32":\n                ctypes.windll.user32.MessageBoxW(0, _msg, "WinZapp", 0x10)\n            else:\n                sys.stderr.write(f"WinZapp startup message (Linux): {_msg}\\n")\n            sys.exit(2)\n        elif _mode == "manager":\n            _msg = _startup_t("startup_account_manager_unavailable")\n            if sys.platform == "win32":\n                ctypes.windll.user32.MessageBoxW(0, _msg, "WinZapp", 0x40)\n            else:\n                sys.stderr.write(f"WinZapp startup message (Linux): {_msg}\\n")\n            sys.exit(0)\n'''
    main = main.replace(error_old, error_new, 1)
    main_changed = True
if main_changed:
    write(main_path, main)
else:
    print("[OK] client/main.py já contém as proteções Linux desta fase")

print("Fase Linux aplicada.")
