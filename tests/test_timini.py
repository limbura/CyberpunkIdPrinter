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
        from unittest.mock import Mock
        from cyberwall_id_printer import TiMiniBackend
        from timiniprint.devices import bluetooth_connection_plan
        from timiniprint.transport.bluetooth import BluetoothDiscovery, DeviceTransport
        from timiniprint.transport.bluetooth.types import DeviceInfo
        from timiniprint.devices.device import BluetoothEndpointTransport

        address = "AA:BB:CC:DD:EE:FF"
        endpoint = DeviceInfo(name="MX10", address=address, transport=DeviceTransport.BLE)
        ble = Mock()
        ble.scan_blocking.return_value = [endpoint]

        async def scenario():
            found = await TiMiniBackend().scan()
            self.assertTrue(any(d["address"] == address for d in found))
            device = await BluetoothDiscovery(self.catalog).resolve_device(address, DeviceTransport.BLE)
            attempts = bluetooth_connection_plan(device).attempts
            self.assertTrue(attempts)
            self.assertTrue(all(a.endpoint.transport == BluetoothEndpointTransport.BLE for a in attempts))

        with patch("timiniprint.transport.bluetooth.backend._get_classic_adapter",
                   side_effect=AssertionError("Classic must not be used")) as classic, \
             patch("timiniprint.transport.bluetooth.backend._get_ble_adapter", return_value=ble):
            asyncio.run(scenario())
            classic.assert_not_called()
