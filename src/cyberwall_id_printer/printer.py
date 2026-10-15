from __future__ import annotations
import asyncio
from .backends.base import PrinterBackend
from .backends.timini import TiMiniBackend
from .errors import ConnectionStateError, PrintTransmissionError, UnsupportedFeature
from .layout import render_for_head
from .renderer import LabelMode
from .models import MediaConfig, PaperType, PrintResult, PrinterCapabilities


class Printer:
    """Async session: one event loop, serialized operations, no automatic retry."""
    def __init__(self, backend: PrinterBackend | None = None, *, media: MediaConfig | None = None):
        self._backend = backend if backend is not None else TiMiniBackend()
        self._media = media or MediaConfig()
        self._capabilities: PrinterCapabilities | None = None
        self._loop = None
        self._lock = asyncio.Lock()
        self._uncertain = False

    def _check_loop(self):
        current = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = current
        elif self._loop is not current:
            raise ConnectionStateError("Use this Printer on a single event loop; use SyncPrinter for synchronous code")

    @property
    def media(self) -> MediaConfig:
        return self._media

    @property
    def capabilities(self) -> PrinterCapabilities:
        if self._capabilities is None:
            raise ConnectionStateError("Printer is not connected")
        return self._capabilities

    def _validate_media(self, media: MediaConfig):
        caps = self.capabilities
        if media.paper_type == PaperType.LABELS and not caps.hardware_labels:
            raise UnsupportedFeature(caps.label_note)
        if media.paper_type == PaperType.CONTINUOUS and not caps.continuous:
            raise UnsupportedFeature("Continuous paper is not supported by this adapter")
        if self._uncertain:
            raise ConnectionStateError("Previous operation may have printed; inspect paper and reconnect before another job")

    async def connect(self, address: str) -> PrinterCapabilities:
        self._check_loop()
        if not address or not address.strip():
            raise ValueError("An explicit printer address or unique name is required")
        async with self._lock:
            if self._capabilities is not None:
                raise ConnectionStateError("Already connected")
            self._capabilities = await self._backend.connect(address)
            self._uncertain = False
            return self.capabilities

    async def configure_media(self, media: MediaConfig) -> None:
        self._check_loop()
        async with self._lock:
            self._validate_media(media)
            self._media = media  # Applied to the next job; no paper motion here.

    async def print_id(self, account_id: str | None = None, name: str | None = None, *,
                       mode: LabelMode = LabelMode.ID_NAME, inactive: bool = True,
                       decorations: bool = True) -> PrintResult:
        self._check_loop()
        async with self._lock:
            self._validate_media(self._media)
            image = render_for_head(account_id, name, self._media,
                                    self.capabilities.head_width_px,
                                    inactive=inactive, decorations=decorations, mode=mode)
            job = self._backend.prepare(image, self._media)  # No I/O, safe validation failure.
            try:
                await self._backend.send(job)
            except asyncio.CancelledError:
                self._uncertain = True
                raise
            except Exception as exc:
                self._uncertain = True
                raise PrintTransmissionError("Transmission failed; some or all of this job may have printed. No retry performed.") from exc
            return PrintResult(image.width, image.height)

    async def calibrate(self) -> None:
        self._check_loop()
        async with self._lock:
            self._validate_media(self._media)
            if self._media.paper_type != PaperType.LABELS:
                raise UnsupportedFeature("Calibration requires label paper")
            if not self.capabilities.calibration:
                raise UnsupportedFeature("Calibration command is not implemented for this profile")
            try:
                await self._backend.calibrate(self._media)
            except BaseException:
                self._uncertain = True
                raise

    async def disconnect(self) -> None:
        self._check_loop()
        async with self._lock:
            try:
                await self._backend.disconnect()
            finally:
                self._capabilities = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()
