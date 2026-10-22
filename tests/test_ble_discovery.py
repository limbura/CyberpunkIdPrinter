"""Real Bleak scan lifecycle and TiMini catalog, with no Bluetooth hardware."""
import asyncio
import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from cyberwall_id_printer import TiMiniBackend
from cyberwall_id_printer.backends.timini import load_timini


@unittest.skipUnless(os.environ.get("CYBERWALL_TIMINI_PATH"),
                     "external TiMini checkout not configured")
class BleDiscoveryTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        load_timini()

    async def asyncSetUp(self):
        from bleak import BleakScanner
        from bleak.backends.scanner import BaseBleakScanner
        from timiniprint.transport.bluetooth import BluetoothDiscovery

        self.loop = asyncio.get_running_loop()
        self.scanners = []
        self.started = asyncio.Queue()
        self.results = []
        self.endpoints = []
        self.address = "AA:BB:CC:DD:EE:FF"
        self.ble_device = SimpleNamespace(address=self.address, name="OS name")
        self.advertisement = SimpleNamespace(local_name="MX10", manufacturer_data={0x1234: b"\x01\x02"})
        owner = self

        class RecordingScanner(BaseBleakScanner):
            def __init__(self, detection_callback, service_uuids, *args, **kwargs):
                super().__init__(detection_callback, service_uuids)
                self.stopped = False
                self.late_callback = asyncio.Event()

            async def start(self):
                self.loop = asyncio.get_running_loop()
                owner.scanners.append(self)
                result = owner.results.pop(0) if owner.results else {}
                if isinstance(result, Exception):
                    raise result
                self.seen_devices = result
                owner.started.put_nowait(self)
                for device, advertisement in result.values():
                    self.loop.call_soon(self.call_detection_callbacks, device, advertisement)

            async def stop(self):
                owner.assertIs(asyncio.get_running_loop(), self.loop)
                await asyncio.sleep(0)
                self.stopped = True

        class TestBleakScanner(BleakScanner):
            def __init__(self, *args, **kwargs):
                super().__init__(*args, backend=RecordingScanner, **kwargs)

        original_resolve = BluetoothDiscovery.devices_from_scan

        def resolve(discovery, endpoints):
            owner.endpoints.extend(endpoints)
            return original_resolve(discovery, endpoints)

        self.enterContext(patch("bleak.BleakScanner", TestBleakScanner))
        self.enterContext(patch.object(BluetoothDiscovery, "devices_from_scan", resolve))
        self.legacy_scan = self.enterContext(patch.object(
            BluetoothDiscovery, "scan_devices",
            side_effect=AssertionError("Temporary TiMini scan loops must not be used")))
        self.enterContext(patch("timiniprint.transport.bluetooth.backend._get_classic_adapter",
                                side_effect=AssertionError("BLE scans must not use Classic")))
        self.connector = self.enterContext(patch(
            "cyberwall_id_printer.backends.ble_connector.connector_with_timeout"))
        self.connected = Mock(disconnect=AsyncMock())

        async def connect(device, connector, **kwargs):
            self.assertIs(asyncio.get_running_loop(), self.loop)
            self.connected.printer_device.return_value = device
            return self.connected

        self.connect = self.enterContext(patch(
            "timiniprint.printing.connected.connect_printer", side_effect=connect))
        self.backend = TiMiniBackend(scan_timeout=0.01, connect_attempts=1)

    async def assert_scanners_on_live_loop(self):
        self.assertTrue(self.scanners)
        for scanner in self.scanners:
            self.assertIs(scanner.loop, self.loop)
            self.assertFalse(scanner.loop.is_closed())
            self.assertTrue(scanner.stopped)
            # Simulate WinRT on_stopped arriving from another thread AFTER
            # the scan has returned. It must still reach the application's loop.
            await asyncio.to_thread(scanner.loop.call_soon_threadsafe, scanner.late_callback.set)
            await asyncio.wait_for(scanner.late_callback.wait(), timeout=1)
        self.legacy_scan.assert_not_called()

    async def test_scan_preserves_advertising_name_and_manufacturer_data(self):
        self.results = [{self.address: (self.ble_device, self.advertisement)}]
        self.assertEqual(await self.backend.scan(), [
            {"name": "MX10", "address": self.address, "profile": "v5g_small_203"}])
        endpoint, = self.endpoints
        self.assertEqual(endpoint.name, "MX10")
        self.assertEqual(endpoint.manufacturer_data, (b"\x34\x12\x01\x02",))
        self.assertEqual(endpoint.transport.value, "ble")
        self.assertIsNone(endpoint.paired)
        await self.assert_scanners_on_live_loop()

    async def test_scan_uses_os_name_when_advertising_name_is_absent(self):
        self.ble_device.name = "MX10"
        self.advertisement.local_name = None
        self.results = [{self.address: (self.ble_device, self.advertisement)}]
        self.assertEqual((await self.backend.scan())[0]["name"], "MX10")
        await self.assert_scanners_on_live_loop()

    async def test_empty_scan_and_repeated_missing_printer_leave_loop_alive(self):
        self.assertEqual(await self.backend.scan(), [])
        for _ in range(2):
            with self.assertRaisesRegex(RuntimeError, "No supported printers found"):
                await self.backend.connect(self.address)
            self.assertIsNone(self.backend._connected)
        self.assertEqual(len(self.scanners), 5)  # standalone + two targeted/full pairs
        self.connect.assert_not_called()
        await self.assert_scanners_on_live_loop()

    async def test_fallback_connect_reuses_windows_device_from_application_loop(self):
        self.results = [{}, {self.address: (self.ble_device, self.advertisement)}]
        with patch("cyberwall_id_printer.backends.timini.sys.platform", "win32"):
            caps = await self.backend.connect(self.address.lower())
        self.assertEqual(caps.address, self.address)
        self.assertEqual(len(self.scanners), 2)
        self.connector.assert_called_once_with(self.backend.timeout, ble_device=self.ble_device)
        await self.backend.disconnect()
        self.connected.disconnect.assert_awaited_once()
        await self.assert_scanners_on_live_loop()

    async def test_targeted_scan_error_still_uses_application_loop_for_fallback(self):
        self.results = [{self.address: (self.ble_device, self.advertisement)}]
        with patch("bleak.BleakScanner.find_device_by_filter", new=AsyncMock(side_effect=OSError("scan failed"))):
            caps = await self.backend.connect(self.address)
        self.assertEqual(caps.address, self.address)
        await self.backend.disconnect()
        await self.assert_scanners_on_live_loop()

    async def test_fallback_does_not_connect_to_another_printer(self):
        self.results = [{}, {self.address: (self.ble_device, self.advertisement)}]
        with self.assertRaisesRegex(RuntimeError, "No device matches"):
            await self.backend.connect("11:22:33:44:55:66")
        self.connect.assert_not_called()
        await self.assert_scanners_on_live_loop()

    async def test_scan_error_propagates_without_connecting(self):
        self.results = [OSError("Bluetooth unavailable")]
        with self.assertRaisesRegex(OSError, "Bluetooth unavailable"):
            await self.backend.scan()
        self.assertIs(self.scanners[0].loop, self.loop)
        self.connect.assert_not_called()

    async def test_cancellation_awaits_scanner_stop_on_same_loop(self):
        # Use the actual Bleak discover context manager: cancelling its sleep
        # must await stop(), not abandon a worker which later closes its loop.
        self.backend.scan_timeout = 60
        for fallback in (False, True):
            with self.subTest(fallback=fallback), patch(
                    "bleak.BleakScanner.find_device_by_filter", new=AsyncMock(return_value=None)):
                operation = self.backend.connect(self.address) if fallback else self.backend.scan()
                task = asyncio.create_task(operation)
                scanner = await asyncio.wait_for(self.started.get(), timeout=1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assertTrue(scanner.stopped)
        self.connect.assert_not_called()
        await self.assert_scanners_on_live_loop()
