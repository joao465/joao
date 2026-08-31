from __future__ import annotations

import atexit
import datetime
import faulthandler
import logging
import os
import platform
import sys
import threading
import traceback

_CRASH = None
_SESSION = None


def _ts() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _portable_dir() -> str:
    if getattr(sys, "frozen", False) or hasattr(sys, "_MEIPASS"):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.getcwd()


class _Tee:
    def __init__(self, original, log_file):
        self.original = original
        self.log_file = log_file

    def write(self, data):
        text = "" if data is None else str(data)
        try:
            if self.original is not None:
                self.original.write(text)
                self.original.flush()
        except Exception:
            pass
        try:
            self.log_file.write(text)
            self.log_file.flush()
        except Exception:
            pass
        return len(text)

    def flush(self):
        try:
            if self.original is not None:
                self.original.flush()
        except Exception:
            pass
        try:
            self.log_file.flush()
        except Exception:
            pass

    def isatty(self):
        try:
            return bool(self.original and self.original.isatty())
        except Exception:
            return False


def _crash_write(message: str) -> None:
    if _CRASH is None:
        return
    try:
        _CRASH.write(message + ("" if message.endswith("\n") else "\n"))
        _CRASH.flush()
    except Exception:
        pass


def install_linux_diagnostics() -> None:
    global _CRASH, _SESSION
    if sys.platform == "win32":
        return

    logdir = _portable_dir()
    os.makedirs(logdir, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    _SESSION = open(
        os.path.join(logdir, f"session-{stamp}.log"),
        "a",
        encoding="utf-8",
        buffering=1,
    )
    _CRASH = open(
        os.path.join(logdir, "crash.log"),
        "a",
        encoding="utf-8",
        buffering=1,
    )

    sys.stdout = _Tee(sys.stdout, _SESSION)
    sys.stderr = _Tee(sys.stderr, _SESSION)

    print("=" * 78)
    print(f"[{_ts()}] WinZapp portable Linux start")
    print(f"pid={os.getpid()} ppid={os.getppid()}")
    print(f"executable={sys.executable}")
    print(f"logdir={logdir}")
    print(f"python={sys.version.replace(chr(10), ' ')}")
    print(f"platform={platform.platform()}")
    print(f"argv={sys.argv!r}")
    print(f"cwd={os.getcwd()}")
    print("=" * 78)

    _crash_write("=" * 78)
    _crash_write(f"[{_ts()}] process start pid={os.getpid()} ppid={os.getppid()}")
    _crash_write(f"executable={sys.executable}")
    _crash_write(f"cwd={os.getcwd()}")

    try:
        faulthandler.enable(file=_CRASH, all_threads=True)
    except Exception:
        _crash_write(traceback.format_exc())

    handler = logging.FileHandler(
        os.path.join(logdir, "python.log"), encoding="utf-8"
    )
    handler.setLevel(logging.DEBUG)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(threadName)s %(name)s: %(message)s"
        )
    )
    root = logging.getLogger()
    root.addHandler(handler)
    if root.level > logging.DEBUG:
        root.setLevel(logging.DEBUG)

    old_hook = sys.excepthook

    def main_hook(exc_type, exc_value, exc_tb):
        _crash_write(f"[{_ts()}] UNHANDLED MAIN EXCEPTION")
        try:
            traceback.print_exception(exc_type, exc_value, exc_tb, file=_CRASH)
        except Exception:
            pass
        try:
            old_hook(exc_type, exc_value, exc_tb)
        except Exception:
            pass

    sys.excepthook = main_hook

    if hasattr(threading, "excepthook"):
        old_thread_hook = threading.excepthook

        def thread_hook(args):
            _crash_write(
                f"[{_ts()}] UNHANDLED THREAD {getattr(args.thread, 'name', None)!r}"
            )
            try:
                traceback.print_exception(
                    args.exc_type, args.exc_value, args.exc_traceback, file=_CRASH
                )
            except Exception:
                pass
            try:
                old_thread_hook(args)
            except Exception:
                pass

        threading.excepthook = thread_hook

    def _finish():
        try:
            print(f"[{_ts()}] Python atexit reached")
        except Exception:
            pass
        _crash_write(f"[{_ts()}] Python atexit reached")

    atexit.register(_finish)
