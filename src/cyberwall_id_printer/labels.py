"""Single-label peel/retract workflow. No automatic replay of physical actions."""
from __future__ import annotations
import asyncio
import logging
import time
from dataclasses import dataclass
from enum import Enum
from math import isfinite
from .errors import PrinterError, ConnectionStateError
from .models import MediaConfig, PaperType, PrintResult
from .renderer import LabelMode
from .layout import render_for_head

# Когда требуется подтверждение снятия стикера после печати (для защиты от зажевывания при обратном втягивании)
class RemovalConfirmation(str, Enum):
    ALWAYS = 'always'   # Подтверждение перед каждым обратным втягиванием
    NEVER = 'never'     # Подтверждений не требуется

# Состояния принтера
class LabelState(str, Enum):
    DISCONNECTED = 'disconnected'
    UNKNOWN = 'unknown'
    READY = 'ready'
    PRESENTED = 'presented'


@dataclass(frozen=True)
class LabelSettings:
    feed_mm: float = 15.0
    retract_mm: float = 25.0
    confirmation: RemovalConfirmation = RemovalConfirmation.ALWAYS
    position_timeout: float = 20.0
    settle_seconds: float = 3.0
    motion_settle_seconds: float = 5.0

    def __post_init__(self):
        object.__setattr__(self, 'confirmation', RemovalConfirmation(self.confirmation))
        for key in ('feed_mm', 'retract_mm', 'position_timeout', 'settle_seconds', 'motion_settle_seconds'):
            value = getattr(self, key)
            if isinstance(value, bool) or not isfinite(value) or value <= 0:
                raise ValueError(f'{key} must be finite and positive')
        if not 0.25 <= self.feed_mm <= 25 or not 0.25 <= self.retract_mm <= 25:
            raise ValueError('Tested feed/retract range is 0.25–25 mm')
        if self.settle_seconds < 2 or self.motion_settle_seconds < 2:
            raise ValueError('settle_seconds must be at least 2 (completion is not acknowledged)')


class RemovalRequired(PrinterError):
    """No pending job is retained. Confirm removal, then submit the job again."""


class LabelOperationError(PrinterError):
    def __init__(self, stage):
        self.stage = stage
        self.may_have_printed = stage in ('printing', 'presenting')
        super().__init__(f'Label operation failed at {stage}; position unknown; no retry performed')


log = logging.getLogger(__name__)


class LabelPrinter:
    """Opt-in V5G labels; distinct from Printer's existing recipes.

    One session/event loop, all public operations serialized. No input() and no
    stored print queue. on_event receives a dict on the application event loop.
    A reconnect never moves paper or resumes an interrupted operation.
    """
    def __init__(self, *, timini_path=None, media=None, settings=None,
                 timeout=20.0, connect_attempts=2, scan_timeout=15.0, on_event=None):
        self.media = media or MediaConfig(paper_type=PaperType.LABELS, roll_width_mm=40)
        if self.media.paper_type != PaperType.LABELS:
            raise ValueError('LabelPrinter requires label media; use Printer for ordinary printing')
        self.settings = settings or LabelSettings()
        self._backend_options = dict(path=timini_path, timeout=timeout,
                                     connect_attempts=connect_attempts, scan_timeout=scan_timeout)
        self._backend = None
        self.capabilities = None
        self.state = LabelState.DISCONNECTED
        self._lock = asyncio.Lock()
        self._loop = None
        self._epoch = None
        self._confirmed = False
        self._last_status = None
        self._on_event = on_event
        self._print_attempt = 0

    def _check_loop(self):
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if loop is not self._loop:
            raise ConnectionStateError('Use LabelPrinter on one event loop')

    def _event(self, kind, **data):
        if self._on_event:
            # Observer failures must not interrupt a mechanical operation.
            try:
                self._on_event(dict(event=kind, state=self.state.value, **data))
            except Exception:
                pass

    def _unknown(self):
        self.state = LabelState.UNKNOWN
        self._confirmed = False
        self._event('position_unknown')

    async def connect(self, address):
        self._check_loop()
        async with self._lock:
            if self.state != LabelState.DISCONNECTED:
                raise ConnectionStateError('Already connected; disconnect before reconnecting')
            if not address or not address.strip():
                raise ValueError('Explicit printer address required')
            if self._backend is None:
                from .backends.timini import load_timini
                load_timini(self._backend_options['path'])
                from .backends.label_timini import LabelTiMiniBackend
                self._backend = LabelTiMiniBackend(**self._backend_options)
            self.capabilities = await self._backend.connect(address)
            self._epoch = None
            self._unknown()
            self._event('connected')
            return self.capabilities

    async def _status(self):
        if self.state == LabelState.DISCONNECTED:
            raise ConnectionStateError('Not connected')
        log.debug("STATUS query start state=%s epoch=%s", self.state.value, self._epoch)
        started = time.monotonic()
        try:
            status, epoch, updated = await self._backend.query_status()
        except BaseException:
            self._unknown()
            raise
        log.debug("STATUS reply status=%s epoch=%s last_epoch=%s elapsed=%.3fs updated=%.6f",
                  status, epoch, self._epoch, time.monotonic() - started, updated)
        if self._epoch is not None and epoch != self._epoch:
            self._unknown()
        self._epoch = epoch
        if status != self._last_status:
            self._last_status = status
            self._event('status', status=status)
        if status not in (0, 0x80):
            self._unknown()
            raise PrinterError(f'Printer not ready, status={status}; check paper/cover, temperature and battery')
        return status

    async def check_connection(self):
        """Liveness probe only; never reconnects or moves paper."""
        self._check_loop()
        async with self._lock:
            if self.state == LabelState.DISCONNECTED:
                return False
            try:
                alive = await self._backend.check_connection()
            except Exception:
                alive = False
            if not alive:
                self._unknown()
            return alive

    async def poll(self):
        """Refresh status without paper motion; also detects idle disconnects."""
        self._check_loop()
        async with self._lock:
            return await self._status()

    async def confirm_removed(self):
        self._check_loop()
        async with self._lock:
            await self._status()
            if self.state != LabelState.PRESENTED:
                raise ConnectionStateError('Removal confirmation is only needed after label presentation')
            self._confirmed = True
            self._event('removal_confirmed')

    async def _position(self):
        log.info("POSITION start state=%s position_timeout=%s", self.state.value, self.settings.position_timeout)
        started = time.monotonic()
        self._event('positioning')
        epoch = self._epoch
        await self._backend.position(self.settings.position_timeout)
        await self._status()
        if self._epoch != epoch:
            raise PrinterError('Printer condition changed during positioning')
        self.state = LabelState.READY
        self._confirmed = False
        log.info("POSITION ready elapsed=%.3fs epoch=%s", time.monotonic() - started, self._epoch)

    async def recover(self):
        """Forward-only recovery. Does not retract, print, or retain a job."""
        self._check_loop()
        async with self._lock:
            try:
                await self._status()
                await asyncio.sleep(0.3)
                await self._status()
                self._unknown()
                await self._position()
                self._event('recovered')
            except BaseException:
                self._unknown()
                raise

    def _require_removal(self):
        mode = self.settings.confirmation
        if self.settings.confirmation == RemovalConfirmation.ALWAYS and not self._confirmed:
            self._event('removal_required')
            raise RemovalRequired('Remove all exposed labels, close cover, confirm removal; then submit print again')

    async def print_image(self, image):
        self._check_loop()
        # Freeze and validate user input before any motion.
        image = image.copy()
        if image.mode != '1' or image.width != 384 or not 1 <= image.height <= round(self.media.height_mm * self.media.dpi / 25.4):
            raise ValueError('Expected monochrome image, width 384 and height within configured label')
        async with self._lock:
            self._print_attempt += 1
            attempt = self._print_attempt
            stage = 'status'
            started = time.monotonic()
            log.info("PRINT_ATTEMPT #%s start state=%s confirmed=%s raster=%sx%s",
                     attempt, self.state.value, self._confirmed, image.width, image.height)
            try:
                await self._status()
                log.info("PRINT_ATTEMPT #%s status_checked state=%s status=%s", attempt, self.state.value, self._last_status)
                if self.state == LabelState.UNKNOWN:
                    stage = 'positioning'
                    log.info("PRINT_ATTEMPT #%s unknown_position: stabilize 0.3s then F0", attempt)
                    await asyncio.sleep(0.3)
                    await self._status()
                    await self._position()  # No blind reverse on recovery.
                if self.state == LabelState.PRESENTED:
                    self._require_removal()
                    stage = 'retracting'
                    self._confirmed = False
                    self._event(stage)
                    await self._backend.move(self.settings.retract_mm, reverse=True,
                                             settle=self.settings.motion_settle_seconds)
                    stage = 'positioning'
                    await self._position()
                self._confirmed = False
                stage = 'printing'
                log.info("PRINT_ATTEMPT #%s raster_phase start elapsed=%.3fs", attempt, time.monotonic() - started)
                self._event(stage)
                await self._backend.print_image(image, self.settings.settle_seconds)
                log.info("PRINT_ATTEMPT #%s raster_phase done elapsed=%.3fs", attempt, time.monotonic() - started)
                stage = 'presenting'
                self._event(stage)
                epoch = self._epoch
                await self._backend.move(self.settings.feed_mm, reverse=False,
                                         settle=self.settings.motion_settle_seconds)
                await self._status()
                if self._epoch != epoch:
                    raise PrinterError('Printer condition changed during presentation')
                self.state = LabelState.PRESENTED
                self._event('presented')
                log.info("PRINT_ATTEMPT #%s success elapsed=%.3fs", attempt, time.monotonic() - started)
                return PrintResult(image.width, image.height)
            except RemovalRequired:
                log.info("PRINT_ATTEMPT #%s removal_required stage=%s elapsed=%.3fs", attempt, stage, time.monotonic() - started)
                raise
            except asyncio.CancelledError:
                self._unknown()
                raise
            except Exception as exc:
                log.exception("PRINT_ATTEMPT #%s failed stage=%s elapsed=%.3fs", attempt, stage, time.monotonic() - started)
                self._unknown()
                raise LabelOperationError(stage) from exc

    async def print_id(self, account_id=None, name=None, *, mode=LabelMode.ID_NAME):
        image = render_for_head(account_id, name, self.media, 384, mode=mode)
        return await self.print_image(image)

    async def disconnect(self):
        self._check_loop()
        async with self._lock:
            try:
                if self._backend:
                    await self._backend.disconnect()
            finally:
                self.state = LabelState.DISCONNECTED
                self.capabilities = None
                self._confirmed = False
                self._event('disconnected')

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        await self.disconnect()
