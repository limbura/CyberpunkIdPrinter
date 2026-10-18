"""Optional integration: actual upstream encoder, no Bluetooth or printer I/O."""
import os
import unittest
from unittest.mock import patch
from cyberwall_id_printer import MediaConfig, PaperType, UnsupportedFeature
from cyberwall_id_printer.layout import render_for_head
from cyberwall_id_printer.backends.timini import load_timini, encode_job, capabilities_for


@unittest.skipUnless(os.environ.get("CYBERWALL_TIMINI_PATH"),"external TiMini checkout not configured")
class TiMiniTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        load_timini()
        from timiniprint.devices import PrinterCatalog
        cls.catalog = PrinterCatalog.load()

    def test_mx10_actual_job_and_pixel_polarity(self):
        device = self.catalog.device_from_key("mx10")
        caps = capabilities_for(device)
        self.assertEqual(caps.profile,"v5g_small_203")
        self.assertFalse(caps.hardware_labels)
        media = MediaConfig()
        image = render_for_head("123456789","Роза фон Праунхайм",media,caps.head_width_px)
        job = encode_job(device,None,image,media)
        self.assertTrue(job.payload or job.steps)
        from timiniprint.protocol.job import PrinterProtocol
        from timiniprint.raster import PixelFormat
        with patch.object(PrinterProtocol,"build_job") as build:
            encode_job(device,None,image,media)
            raster = build.call_args.args[0].require(PixelFormat.BW1)
            self.assertEqual(list(raster.pixels),[int(p == 0) for p in image.convert("L").tobytes()])
            self.assertEqual(build.call_args.kwargs["pixel_format_override"],PixelFormat.BW1)
        with self.assertRaises(UnsupportedFeature):
            encode_job(device,None,image,MediaConfig(paper_type=PaperType.LABELS))

    def test_v5x_tag_recipe(self):
        devices = [self.catalog.device_from_model(m.model_key) for m in self.catalog.models]
        device = next(d for d in devices if d.protocol_family.value == "v5x")
        caps = capabilities_for(device)
        self.assertTrue(caps.hardware_labels)
        media = MediaConfig(paper_type=PaperType.LABELS)
        image = render_for_head("123456789","РОЗА",media,caps.head_width_px)
        job = encode_job(device,None,image,media)
        self.assertTrue(job.payload or job.steps)
        self.assertFalse(caps.calibration)

    def test_ble_dependencies_import_without_winsdk(self):
        import importlib
        import sys
        from importlib.metadata import requires
        from cyberwall_id_printer.backends.timini import _imports
        # Verify package metadata as well as the actual platform BLE imports.
        self.assertFalse(any(r.lower().startswith("winsdk")
                             for r in requires("cyberwall_id_printer")))
        _imports(None)
        from bleak import BleakClient, BleakScanner
        self.assertTrue(callable(BleakClient))
        self.assertTrue(callable(BleakScanner))
        if sys.platform == "win32":
            importlib.import_module("bleak.backends.winrt.client")
            importlib.import_module("bleak.backends.winrt.scanner")
        self.assertFalse(any(n == "winsdk" or n.startswith("winsdk.") for n in sys.modules))

    def test_scan_and_connection_plan_never_use_classic(self):
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, Mock
        from cyberwall_id_printer import TiMiniBackend
        from timiniprint.devices import bluetooth_connection_plan
        from timiniprint.transport.bluetooth import DeviceTransport
        from timiniprint.transport.bluetooth.types import DeviceInfo
        from timiniprint.devices.device import BluetoothEndpointTransport

        address = "AA:BB:CC:DD:EE:FF"
        endpoint = DeviceInfo(name="MX10", address=address, transport=DeviceTransport.BLE)
        ble = Mock()
        # Simulate a printer which is not discovered within the old 5s window.
        ble.scan_blocking.side_effect = lambda timeout: [endpoint] if timeout >= 15 else []
        connected = Mock()
        connected.disconnect = AsyncMock()

        async def connect(device, connector, **kwargs):
            connected.printer_device.return_value = device
            return connected

        ble_device = SimpleNamespace(address=address, name=None)

        async def find_device(predicate, *, timeout):
            self.assertEqual(timeout, 15)
            self.assertFalse(predicate(
                SimpleNamespace(address="00:00:00:00:00:00", name="MX10"),
                SimpleNamespace(local_name="MX10")))
            self.assertFalse(predicate(ble_device, SimpleNamespace(local_name=None)))
            self.assertTrue(predicate(ble_device, SimpleNamespace(local_name="MX10")))
            return ble_device

        async def scenario():
            backend = TiMiniBackend()
            found = await backend.scan()
            self.assertTrue(any(d["address"] == address for d in found))
            caps = await backend.connect(address)
            self.assertEqual(caps.address, address)
            device = connected.printer_device()
            attempts = bluetooth_connection_plan(device).attempts
            self.assertTrue(attempts)
            self.assertTrue(all(a.endpoint.transport == BluetoothEndpointTransport.BLE for a in attempts))
            await backend.disconnect()

        with patch("timiniprint.transport.bluetooth.backend._get_classic_adapter",
                   side_effect=AssertionError("Classic must not be used")) as classic, \
             patch("timiniprint.transport.bluetooth.backend._get_ble_adapter", return_value=ble), \
             patch("timiniprint.printing.connected.connect_printer", side_effect=connect), \
             patch("bleak.BleakScanner.find_device_by_filter",
                   new=AsyncMock(side_effect=find_device)) as targeted_scan:
            asyncio.run(scenario())
            classic.assert_not_called()
            targeted_scan.assert_awaited_once()
            ble.scan_blocking.assert_called_once()

    def test_actual_batch_keeps_page_metadata_and_protocol_steps(self):
        from unittest.mock import Mock
        from cyberwall_id_printer import TiMiniBackend
        from timiniprint.protocol.job import PrinterProtocol
        from timiniprint.printing.raster_job import combine_raster_page_jobs
        device = self.catalog.device_from_key('mx10')
        backend = TiMiniBackend()
        connected = Mock()
        connected.printer_device.return_value = device
        connected.print_capabilities.return_value = None
        connected.raster_pages_job.side_effect = combine_raster_page_jobs
        backend._connected = connected
        image = render_for_head('123456789', 'РОЗА', MediaConfig(), 384)
        original = PrinterProtocol.build_job
        metadata, pages = [], []
        def build(protocol, *args, **kwargs):
            metadata.append((kwargs['page_index'], kwargs['page_count']))
            job = original(protocol, *args, **kwargs)
            pages.append(job)
            return job
        with patch.object(PrinterProtocol, 'build_job', build):
            job = backend.prepare_copies(image, MediaConfig(), 10)
        self.assertEqual(metadata, [(i,10) for i in range(1,11)])
        self.assertEqual(job.payload, b''.join(page.payload for page in pages))
        self.assertEqual(job.steps, tuple(step for page in pages for step in page.steps))

    def test_idle_ble_monitor_pumps_socket_loop_and_reads_real_client_state(self):
        import asyncio
        from types import SimpleNamespace
        from cyberwall_id_printer import TiMiniBackend
        class Transport:
            _sock = None
            def is_connected(self): return True
            async def _run_blocking(self, function):
                return await asyncio.to_thread(function)
        transport = Transport()
        backend = TiMiniBackend()
        backend._connected = SimpleNamespace(_connection=SimpleNamespace(_backend=transport))
        loop = asyncio.new_event_loop()
        client = SimpleNamespace(is_connected=True)
        transport._sock = SimpleNamespace(_client=client, _loop=loop)
        async def scenario():
            self.assertTrue(await backend.check_connection())
            # Delivered on the otherwise-idle upstream BLE event loop.
            loop.call_soon(setattr, client, 'is_connected', False)
            self.assertFalse(await backend.check_connection())
        try:
            asyncio.run(scenario())
        finally:
            loop.close()

