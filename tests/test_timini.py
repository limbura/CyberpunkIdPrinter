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
