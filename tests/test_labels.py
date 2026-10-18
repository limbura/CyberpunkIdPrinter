import asyncio
import os
import unittest
from unittest.mock import AsyncMock, Mock, patch
from PIL import Image
from cyberwall_id_printer import (LabelPrinter, LabelSettings, LabelState, RemovalRequired,
                                  LabelOperationError, TiMiniBackend, ConnectionStateError)


class FakeBackend:
    def __init__(self):
        self.calls = []
        self.epoch = 0
        self.status = 0
        self.fail = None
    async def connect(self, address): return Mock(head_width_px=384)
    async def disconnect(self): self.calls.append('disconnect')
    async def query_status(self): return self.status, self.epoch, 0
    async def position(self, timeout):
        self.calls.append('F0')
        if self.fail == 'position': raise TimeoutError()
    async def move(self, mm, *, reverse, settle):
        self.calls.append(('reverse' if reverse else 'feed', mm))
        if self.fail == 'feed' and not reverse: raise OSError()
    async def print_image(self, image, settle):
        self.calls.append('print')
        if self.fail == 'print': raise OSError()
        if self.fail == 'cancel': raise asyncio.CancelledError()


class LabelTests(unittest.IsolatedAsyncioTestCase):
    async def create(self, mode='always'):
        p = LabelPrinter(settings=LabelSettings(confirmation=mode))
        p._backend = FakeBackend()
        await p.connect('test')
        return p

    async def test_cycle_retract_is_start_of_next_print(self):
        p = await self.create()
        image = Image.new('1', (384, 24), 1)
        self.assertEqual(p.state, LabelState.UNKNOWN)
        self.assertEqual(p._backend.calls, [])
        await p.print_image(image)
        self.assertEqual(p._backend.calls, ['F0', 'print', ('feed', 15)])
        before = list(p._backend.calls)
        with self.assertRaises(RemovalRequired): await p.print_image(image)
        self.assertEqual(p._backend.calls, before)
        self.assertEqual(p.state, LabelState.PRESENTED)
        await p.confirm_removed()
        await p.print_image(image)
        self.assertEqual(p._backend.calls[-4:], [('reverse', 25), 'F0', 'print', ('feed', 15)])

    async def test_motion_wait_is_independent_and_finishes_before_f0(self):
        p = await self.create('never')
        p.settings = LabelSettings(confirmation='never', motion_settle_seconds=10)
        image = Image.new('1', (384, 24), 1)
        await p.print_image(image)
        events = []
        async def move(mm, *, reverse, settle):
            events.append(('motion_start', reverse, settle))
            await asyncio.sleep(0)
            events.append('motion_finished')
        async def position(timeout):
            self.assertEqual(events[-1], 'motion_finished')
            events.append('F0')
        p._backend.move = move
        p._backend.position = position
        await p.print_image(image)
        self.assertEqual(events[:3], [('motion_start', True, 10), 'motion_finished', 'F0'])

    async def test_never_mode_does_not_require_confirmation(self):
        image = Image.new('1', (384, 24), 1)
        p = await self.create('never')
        await p.print_image(image)
        await p.print_image(image)
        self.assertEqual(p._backend.calls, [
            'F0', 'print', ('feed', 15),
            ('reverse', 25), 'F0', 'print', ('feed', 15),
        ])

    async def test_error_normal_burst_invalidates_position_and_confirmation(self):
        p = await self.create()
        image = Image.new('1', (384, 24), 1)
        await p.print_image(image)
        await p.confirm_removed()
        self.assertTrue(p._confirmed)
        p._backend.epoch += 2  # Two faults, already back to normal.
        await p.poll()
        self.assertEqual(p.state, LabelState.UNKNOWN)
        self.assertFalse(p._confirmed)
        before = len(p._backend.calls)
        await p.print_image(image)
        self.assertEqual(p._backend.calls[before:], ['F0', 'print', ('feed', 15)])
        self.assertEqual(p.state, LabelState.PRESENTED)

    async def test_fault_does_not_kill_session_and_cannot_confirm_unknown(self):
        p = await self.create()
        p._backend.status = 1
        p._backend.epoch += 1
        with self.assertRaises(RuntimeError): await p.poll()
        p._backend.status = 0
        await p.poll()
        with self.assertRaises(RuntimeError): await p.confirm_removed()
        await p.recover()
        self.assertEqual(p.state, LabelState.READY)
        self.assertEqual(p._backend.calls, ['F0'])
        with self.assertRaises(ConnectionStateError):
            await p.confirm_removed()
        await p.print_image(Image.new('1', (384, 24), 1))
        self.assertEqual(p._backend.calls, ['F0', 'print', ('feed', 15)])

    async def test_no_replay_after_failed_print_or_presentation(self):
        for stage, expected in [('print', 'printing'), ('feed', 'presenting')]:
            p = await self.create('never')
            p._backend.fail = stage
            with self.assertRaises(LabelOperationError) as error:
                await p.print_image(Image.new('1', (384, 24)))
            self.assertEqual(error.exception.stage, expected)
            self.assertTrue(error.exception.may_have_printed)
            self.assertEqual(p._backend.calls.count('print'), 1)
            self.assertEqual(p.state, LabelState.UNKNOWN)

    async def test_cancellation_invalidates_position(self):
        p = await self.create('never')
        p._backend.fail = 'cancel'
        with self.assertRaises(asyncio.CancelledError):
            await p.print_image(Image.new('1', (384, 24)))
        self.assertEqual(p.state, LabelState.UNKNOWN)

    async def test_reconnect_does_not_move_or_keep_confirmation(self):
        p = await self.create()
        image = Image.new('1', (384, 24), 1)
        await p.print_image(image)
        await p.confirm_removed()
        self.assertTrue(p._confirmed)
        await p.disconnect()
        before = list(p._backend.calls)
        await p.connect('test')
        self.assertEqual(p._backend.calls, before)
        self.assertEqual(p.state, LabelState.UNKNOWN)
        self.assertFalse(p._confirmed)
        await p.print_image(image)
        self.assertEqual(p._backend.calls[len(before):], ['F0', 'print', ('feed', 15)])

    async def test_invalid_image_never_moves(self):
        p = await self.create('never')
        with self.assertRaises(ValueError): await p.print_image(Image.new('1', (384, 241)))
        self.assertEqual(p._backend.calls, [])

    async def test_failed_position_never_prints_or_reverses(self):
        p = await self.create('never')
        p._backend.fail = 'position'
        with self.assertRaises(LabelOperationError): await p.print_image(Image.new('1', (384, 24)))
        self.assertEqual(p._backend.calls, ['F0'])

    async def test_connect_retry_only_before_jobs(self):
        b = TiMiniBackend()
        self.assertEqual(b.timeout, 20)
        b._connect_once = AsyncMock(side_effect=[OSError('cancelled'), 'caps'])
        with patch('asyncio.sleep', new=AsyncMock()):
            self.assertEqual(await b.connect('test'), 'caps')
        self.assertEqual(b._connect_once.await_count, 2)
        b._connect_once = AsyncMock(side_effect=OSError())
        with patch('asyncio.sleep', new=AsyncMock()):
            with self.assertRaises(OSError): await b.connect('test')
        self.assertEqual(b._connect_once.await_count, 2)

    def test_settings_validation(self):
        for kwargs in ({'feed_mm': 26}, {'retract_mm': 0}, {'settle_seconds': 0.1}, {'confirmation': 'yes'}, {'confirmation': 'recovery'}):
            with self.assertRaises(ValueError): LabelSettings(**kwargs)


@unittest.skipUnless(os.environ.get('CYBERWALL_TIMINI_PATH'), 'TiMini not configured')
class RuntimeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        from cyberwall_id_printer.backends.timini import load_timini
        load_timini()

    async def test_status_recovery_and_operation_fault_latch(self):
        from cyberwall_id_printer.backends.label_timini import LabelRuntime, packet
        runtime, session = LabelRuntime(), Mock()
        session._wait_for_flow = AsyncMock()
        runtime.handle_notification(session, packet(0xA3, b'\0\x03\x32'))
        with self.assertRaises(RuntimeError):
            with runtime.operation():
                runtime.handle_notification(session, packet(0xA3, b'\x01\x03\x98'))
                runtime.handle_notification(session, packet(0xA3, b'\0\x03\x32'))
                await runtime.before_write(session, size=10, timeout=1)
        # The old error must not poison the next recovered operation.
        with runtime.operation():
            await runtime.before_write(session, size=10, timeout=1)
        self.assertEqual(runtime.snapshot()[1], 1)

    def test_recipe_matches_captured_mark_without_extra_feed(self):
        from cyberwall_id_printer.backends.label_timini import image_packets, packet, FAMILY
        from timiniprint.protocol.packet import PrefixedPacketStreamDecoder
        image = Image.new('1', (384, 24), 1)
        image.putpixel((0, 0), 0)
        frames = PrefixedPacketStreamDecoder(FAMILY).feed(b''.join(image_packets(image)))
        self.assertEqual([f.opcode for f in frames], [0xF2,0xA3,0xA4,0xA6,0xAF,0xBE,0xBD] + [0xA2]*24 + [0xBD,0xA6,0xA3,0xA3])
        self.assertEqual(frames[7].payload, b'\x01' + bytes(47))
        self.assertEqual(packet(0xF0, bytes.fromhex('008320')).hex(), '5178f000030000832069ff')

    async def test_real_connector_passes_timeout_and_closes_failed_socket(self):
        from cyberwall_id_printer.backends.ble_connector import connector_with_timeout
        from timiniprint.devices import PrinterCatalog
        from timiniprint.transport.bluetooth.discovery import BluetoothDiscovery
        from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport
        catalog = PrinterCatalog.load()
        device = BluetoothDiscovery(catalog).devices_from_scan(
            [DeviceInfo(name='MX10', address='EB:11:06:26:77:26', transport=DeviceTransport.BLE)])[0]
        sock = Mock()
        sock.connect.side_effect = OSError('cancelled')
        with patch('timiniprint.transport.bluetooth.adapters.bleak_adapter._BleakSocket', return_value=sock):
            with self.assertRaises(OSError):
                await connector_with_timeout(23).connect(device)
        sock.settimeout.assert_called_once_with(23)
        sock.close.assert_called_once()

    def test_full_cross_matches_successful_experiment_byte_for_byte(self):
        import importlib.util
        from pathlib import Path
        from cyberwall_id_printer.backends.label_timini import image_packets
        root = Path(__file__).resolve().parents[1]
        def module(name, path):
            spec = importlib.util.spec_from_file_location(name, path)
            result = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(result)
            return result
        old = module('original_label_experiment', root / 'other' / 'thermoprinter_label_test.py')
        example = module('label_v2_example', root / 'examples' / 'labels_v2.py')
        self.assertEqual(tuple(image_packets(example.mark())), tuple(old.print_packets()))

    async def test_raster_uses_one_standard_send_without_extra_feed(self):
        from cyberwall_id_printer.backends.label_timini import LabelTiMiniBackend, LabelRuntime, image_packets, packet
        from types import SimpleNamespace
        backend = LabelTiMiniBackend()
        connection = SimpleNamespace(send_standard_payload=AsyncMock(), send_control_packet=AsyncMock())
        backend._connected = SimpleNamespace(_connection=connection)
        backend.runtime = LabelRuntime()
        backend.runtime.handle_notification(Mock(), packet(0xA3, b'\0'))
        backend.pump = AsyncMock()
        image = Image.new('1', (384, 240), 1)
        await backend.print_image(image, 3)
        connection.send_standard_payload.assert_awaited_once_with(b''.join(image_packets(image)))
        connection.send_control_packet.assert_not_awaited()
        backend.pump.assert_awaited_once_with(3)

