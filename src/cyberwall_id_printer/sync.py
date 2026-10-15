"""Blocking facade for scripts. GUI applications should use a worker thread."""
import asyncio
import threading
from .printer import Printer
from .errors import ConnectionStateError


class SyncPrinter:
    """One persistent event loop; use and close on its creating thread.

    Cannot be called inside a running asyncio loop. Use Printer there instead.
    """
    def __init__(self, backend=None, *, media=None):
        self._printer = Printer(backend, media=media)
        self._runner = asyncio.Runner()
        self._thread = threading.get_ident()
        self._closed = False

    def _check(self):
        if self._closed or threading.get_ident() != self._thread:
            raise ConnectionStateError("SyncPrinter is closed or used from another thread")
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        raise ConnectionStateError("Use async Printer inside a running event loop")

    def _run(self, method, *args, **kwargs):
        self._check()
        return self._runner.run(method(*args, **kwargs))

    @property
    def capabilities(self):
        return self._printer.capabilities

    @property
    def media(self):
        return self._printer.media

    def connect(self, address):
        return self._run(self._printer.connect, address)

    def configure_media(self, media):
        return self._run(self._printer.configure_media, media)

    def print_id(self, account_id=None, name=None, **kwargs):
        return self._run(self._printer.print_id, account_id, name, **kwargs)

    def calibrate(self):
        return self._run(self._printer.calibrate)

    def disconnect(self):
        return self._run(self._printer.disconnect)

    def close(self):
        if self._closed:
            return
        self._check()
        try:
            self.disconnect()
        finally:
            self._runner.close()
            self._closed = True

    def __enter__(self):
        self._check()
        return self

    def __exit__(self, *exc):
        self.close()
