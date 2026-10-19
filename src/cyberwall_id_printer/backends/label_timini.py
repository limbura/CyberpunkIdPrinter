"""Experimental, hardware-observed V5G label recipe over TiMini's transport.

This module is lazy-loaded. TiMini owns BLE, framing, flow control and status
semantics; the explicit recipe deliberately has no standard end-of-page feed.
"""
from contextlib import contextmanager
from threading import Lock
import asyncio
import time
import logging

log = logging.getLogger(__name__)
from timiniprint.protocol.family import ProtocolFamily
from timiniprint.protocol.packet import make_packet, PrefixedPacketStreamDecoder
from timiniprint.protocol.encoding import pack_line
from timiniprint.protocol.families.v5g import decode_error_status
from timiniprint.printing.runtime.v5g import V5GRuntimeController
from .timini import TiMiniBackend
from ..errors import UnsupportedFeature, PrinterError, DeviceStatusError

FAMILY = ProtocolFamily.V5G


def packet(op, data):
    return make_packet(op, data, FAMILY)


class LabelRuntime(V5GRuntimeController):
    def __init__(self):
        super().__init__()
        self._observer = PrefixedPacketStreamDecoder(FAMILY)
        self._guard = Lock()
        self._status = None
        self._epoch = 0
        self._armed_epoch = None
        self._updated = 0.0
        self._last_fault = None

    def snapshot(self):
        with self._guard:
            return self._status, self._epoch, self._updated

    def handle_notification(self, session, payload):
        log.debug("RX %s", payload.hex(" "))
        super().handle_notification(session, payload)
        for frame in self._observer.feed(payload):
            if frame.opcode == 0xA3 and frame.payload:
                with self._guard:
                    self._status = frame.payload[0]
                    self._updated = time.monotonic()
                    # Preserve evidence even if error/normal arrive in one read.
                    if self._status not in (0, 0x80):
                        self._epoch += 1
                        self._last_fault = self._status

    def check(self):
        with self._guard:
            status, epoch = self._status, self._epoch
            armed = self._armed_epoch
            fault = self._last_fault
        if status not in (0, 0x80):
            reason = decode_error_status(status) if status is not None else None
            raise DeviceStatusError(reason.value if reason else "unknown", status)
        if armed is not None and armed != epoch:
            reason = decode_error_status(fault) if fault is not None else None
            raise DeviceStatusError(reason.value if reason else "unknown", fault)

    async def before_write(self, session, *, size, timeout):
        with self._guard:
            armed = self._armed_epoch
        if armed is not None:
            self.check()
            # Control writes in pinned TiMini do not wait for AE flow by default.
            # Reuse its BLE flow wait here, including its timeout.
            await session._wait_for_flow(timeout)
            self.check()
        await super().before_write(session, size=size, timeout=timeout)

    @contextmanager
    def operation(self):
        self.check()
        with self._guard:
            self._armed_epoch = self._epoch
        try:
            self.check()
            yield
            self.check()
        finally:
            with self._guard:
                self._armed_epoch = None


def image_packets(image):
    if image.mode != '1' or image.width != 384:
        raise ValueError('Label recipe expects a monochrome 384-dot raster')
    yield packet(0xF2, bytes.fromhex('01c8'))
    yield packet(0xA3, b'\0')
    yield packet(0xA4, b'\x34')
    yield packet(0xA6, bytes.fromhex('aa551738445f5f5f44382c'))
    yield packet(0xAF, bytes.fromhex('983a'))
    yield packet(0xBE, b'\0')
    yield packet(0xBD, b'\x0a')
    pixels = image.convert('L').tobytes()
    for y in range(image.height):
        row = [int(p == 0) for p in pixels[y * 384:(y + 1) * 384]]
        yield packet(0xA2, pack_line(row, lsb_first=True))
    yield packet(0xBD, b'\x0a')
    yield packet(0xA6, bytes.fromhex('aa55170000000000000017'))
    yield packet(0xA3, b'\0')
    yield packet(0xA3, b'\0')


class LabelTiMiniBackend(TiMiniBackend):
    runtime = None

    def _connection_options(self, device):
        # Opt-in recipe; do not claim support for every V5G device.
        if device.profile_key != 'v5g_small_203' or device.protocol_family != FAMILY:
            raise UnsupportedFeature('Experimental labels require the tested v5g_small_203 profile')
        self.runtime = LabelRuntime()
        return {'controller': self.runtime}

    @property
    def connection(self):
        if self._connected is None:
            raise PrinterError('Printer is disconnected')
        # Pinned TiMini boundary; raw control operations are exposed here only.
        return self._connected._connection

    @staticmethod
    def status_reason(status):
        if status in (0, 0x80):
            return "normal"
        reason = decode_error_status(status) if status is not None else None
        return reason.value if reason else "unknown"

    async def query_status(self):
        decoder = PrefixedPacketStreamDecoder(FAMILY)
        def match(data):
            return any(f.opcode == 0xA3 and f.payload
                       for f in decoder.feed(data))
        await self.connection.send_control_packet_wait_notification(
            packet(0xA3, b'\0'), label='label status', match=match,
            timeout=self.timeout, required=True)
        return self.runtime.snapshot()

    async def position(self, timeout):
        decoder = PrefixedPacketStreamDecoder(FAMILY)
        def match(data):
            return any(f.opcode == 0xF0 and f.payload == b'\x01'
                       for f in decoder.feed(data))
        with self.runtime.operation():
            log.debug("TX F0 positioning start")
            await self.connection.send_control_packet_wait_notification(
                packet(0xF0, bytes.fromhex('008320')), label='label positioning F0',
                match=match, timeout=timeout, required=True)
            await self.pump(0.5)

    async def pump(self, seconds):
        # TiMini's BLE loop lives on its serialized worker, not our event loop.
        # Pump it while waiting so faults/flow notifications are not left queued.
        end = time.monotonic() + seconds
        while time.monotonic() < end:
            if not await self.check_connection():
                raise ConnectionError('Bluetooth disconnected')
            self.runtime.check()
            await asyncio.sleep(min(0.05, max(0, end - time.monotonic())))

    async def move(self, mm, *, reverse, settle):
        # Observed ~0.25 mm/unit, saturation around 100. Not a universal DPI rule.
        units = round(mm * 4)
        log.debug("Motion reverse=%s mm=%s units=%s", reverse, mm, units)
        with self.runtime.operation():
            sent = await self.connection.send_control_packet(
                packet(0xA0 if reverse else 0xA1, units.to_bytes(2, 'little')),
                timeout=self.timeout)
            if not sent:
                raise RuntimeError("TiMini control write unavailable")
            log.debug("Motion write returned; waiting %s seconds (not completion ACK)", settle)
            await self.pump(settle)
            log.debug("Motion wait finished; reverse=%s", reverse)

    async def print_image(self, image, settle):
        packets = tuple(image_packets(image))  # Validate/build before sending.
        with self.runtime.operation():
            payload = b''.join(packets)
            log.debug("Raster send start: rows=%s bytes=%s transport=TiMini-standard", image.height, len(payload))
            await self.connection.send_standard_payload(payload)
            log.debug("Raster send finished; settling=%s (not completion ACK)", settle)
            await self.pump(settle)

