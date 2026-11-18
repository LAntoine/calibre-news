"""Tiny logging facility compatible with the bits of calibre.utils.logging recipes use."""

from __future__ import annotations

import sys
import threading


class Log:
    def __init__(self, out=None, callback=None) -> None:
        self.out = out or sys.stderr
        self.callback = callback
        self._lock = threading.Lock()
        self.records: list[tuple[str, str]] = []

    def _emit(self, level: str, msg) -> None:
        line = "%s: %s" % (level, msg)
        with self._lock:
            self.records.append((level, str(msg)))
            print(line, file=self.out)
            if self.callback is not None:
                try:
                    self.callback(level, str(msg))
                except Exception:
                    pass

    def __call__(self, *args) -> None:
        self._emit("INFO", " ".join(str(a) for a in args))

    def info(self, *args) -> None:
        self.__call__(*args)

    def warning(self, *args) -> None:
        self._emit("WARNING", " ".join(str(a) for a in args))

    warn = warning

    def error(self, *args) -> None:
        self._emit("ERROR", " ".join(str(a) for a in args))

    def debug(self, *args) -> None:
        self._emit("DEBUG", " ".join(str(a) for a in args))

    def exception(self, *args) -> None:
        import traceback

        self._emit("ERROR", " ".join(str(a) for a in args))
        traceback.print_exc(file=self.out)


class ThreadSafeWrapper:
    def __init__(self, log: Log) -> None:
        self._log = log

    def __getattr__(self, name):
        return getattr(self._log, name)

    def __call__(self, *args) -> None:
        self._log(*args)


default_log = Log()
