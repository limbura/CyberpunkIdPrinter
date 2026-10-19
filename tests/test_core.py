import asyncio
import unittest
from PIL import Image
from cyberwall_id_printer import (Printer, SyncPrinter, MediaConfig, PaperType,
    LabelConfig, LabelMode, render_label, format_id, normalize_name, PrinterCapabilities,
    UnsupportedFeature, PrintTransmissionError, ConnectionStateError)
from cyberwall_id_printer.layout import render_for_head


class RendererTests(unittest.TestCase):
    def test_fixed_templates_and_digit_positions(self):
        filled = render_label('123456789', 'Роза фон Праунхайм')
        blank = render_label(config=LabelConfig(mode=LabelMode.BLANK_ID_NAME))
        # Unused cells have exactly the same guide on filled and blank labels.
        for row, length in enumerate((4,3,9)):
            area = (26+length*25, 108+row*37, 296, 143+row*37)
            self.assertEqual(filled.crop(area).tobytes(), blank.crop(area).tobytes())
            self.assertEqual(blank.crop(area).convert('L').getextrema(), (0,255))
        filled = render_label('123456789', config=LabelConfig(mode=LabelMode.ID_ONLY))
        blank = render_label(config=LabelConfig(mode=LabelMode.BLANK_ID_ONLY))
        for row, start in enumerate((0,3,2)):
            for col in range(6):
                area = (22+47*col,21+66*row,64+47*col,85+66*row)
                same = filled.crop(area).tobytes() == blank.crop(area).tobytes()
                self.assertEqual(same, not start <= col < start+3)
        self.assertLess(blank.crop((22,87,112,151)).convert('L').getextrema()[0],255)

    def test_content_constraints_and_scaling(self):
        render_label('001002003', 'А'*11+' '+'Б'*11+' '+'Я'*11)
        for name in ('А Б В Г', 'А'*12):
            with self.assertRaises(ValueError): render_label('123456789', name)
        for mode in (LabelMode.BLANK_ID_NAME, LabelMode.BLANK_ID_ONLY):
            with self.assertRaises(ValueError): render_label('123456789', config=LabelConfig(mode=mode))
            with self.assertRaises(ValueError): render_label(config=LabelConfig(mode=mode,inactive=False))
        with self.assertRaises(ValueError):
            render_label('123456789','РОЗА',LabelConfig(mode=LabelMode.ID_ONLY))
        for mode in LabelMode:
            is_blank = mode.value.startswith('blank')
            for w,h in ((40,30),(45,35),(30,40)):
                im = render_label(None if is_blank else '001002003',
                                  'ЁЖ ЙОД ЩУКА' if mode==LabelMode.ID_NAME else None,
                                  LabelConfig(w,h,mode=mode))
                self.assertEqual(im.size,(w*8,h*8))
                self.assertEqual(im.mode,'1')
                self.assertEqual(im.crop((0,0,im.width,5)).convert('L').getextrema(),(255,255))

    def test_sample_and_exact_padding(self):
        image = render_label("123456789", "Роза фон Праунхайм")
        self.assertEqual((image.mode, image.size), ("1", (320,240)))
        padded = render_for_head("123456789", "Роза фон Праунхайм", MediaConfig(), 384)
        self.assertEqual(padded.crop((32,0,352,240)).tobytes(), image.tobytes())
        self.assertEqual(padded.crop((0,0,32,240)).getextrema(), (1,1))

    def test_names_and_id(self):
        self.assertEqual(format_id("001002003"), "001 002 003")
        self.assertEqual(normalize_name("  Роза  фон Праунхайм "), "РОЗА ФОН ПРАУНХАЙМ")
        for ch in "АБВГДЕЁЖЗИЙКЛМНОПРСТУФХЦЧШЩЪЫЬЭЮЯ":
            self.assertEqual(render_label("000000000", ch).mode, "1")
        for value in ("123", "１２３４５６７８９", "123 456 789"):
            with self.assertRaises(ValueError): format_id(value)
        for name in ("", "А" * 100):
            with self.assertRaises(ValueError): render_label("123456789", name)

    def test_unknown_name_characters_render_as_underscores(self):
        for name, expected in [('Роза "Лис"', 'РОЗА _ЛИС_'),
                               ('А🦊Б@В', 'А_Б_В'), ('🦊', '_'),
                               ('А-Б_В', 'А-Б_В'), ('  Ёж\tЙод  ', 'ЁЖ ЙОД')]:
            with self.subTest(name=name):
                self.assertEqual(normalize_name(name), expected)
                self.assertEqual(render_label('123456789', name).tobytes(),
                                 render_label('123456789', expected).tobytes())
                self.assertEqual(render_for_head('123456789', name, MediaConfig(), 384).size,
                                 (384, 240))

    def test_underscore_and_hyphen_are_distinct_visible_strokes(self):
        from PIL import ImageDraw
        from cyberwall_id_printer.renderer import glyph
        images = []
        for char in '_-':
            image = Image.new('1', (40, 50), 1)
            glyph(ImageDraw.Draw(image), char, 5, 5, 20, 30, 2, False)
            dark = image.convert('L').point(lambda value: 255 - value)
            images.append(dark.getbbox())
        self.assertIsNotNone(images[0])
        self.assertIsNotNone(images[1])
        self.assertGreater(images[0][1], images[1][3])

    def test_bounds(self):
        for v in (float("nan"), float("inf"), -1, 0):
            with self.assertRaises(ValueError): render_label("123456789", "РОЗА", LabelConfig(width_mm=v))
            with self.assertRaises(ValueError): MediaConfig(width_mm=v)
        with self.assertRaises(ValueError): MediaConfig(width_mm=58, roll_width_mm=40)
        for media in (MediaConfig(width_mm=50), MediaConfig(offset_x_mm=20)):
            with self.assertRaises(ValueError): render_for_head("123456789", "РОЗА", media,384)


class FakeBackend:
    def __init__(self, *, fail=False, labels=False, calibration=False):
        self.fail, self.labels, self.calibration = fail, labels, calibration
        self.sends = self.active = self.peak = self.calibrations = 0
        self.loops = []
        self.disconnected = False

    async def connect(self, address):
        self.loops.append(asyncio.get_running_loop())
        return PrinterCapabilities("Fake", address, "fake",384,203.2,
                                   hardware_labels=self.labels,calibration=self.calibration)

    async def disconnect(self):
        self.loops.append(asyncio.get_running_loop())
        self.disconnected = True

    def prepare(self, image, media):
        return image

    async def send(self, job):
        self.loops.append(asyncio.get_running_loop())
        self.sends += 1
        self.active += 1
        self.peak = max(self.peak, self.active)
        try:
            await asyncio.sleep(0.001)
            if self.fail: raise OSError("lost connection")
        finally:
            self.active -= 1

    async def calibrate(self, media):
        self.calibrations += 1


class SessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_serialization_and_result(self):
        backend = FakeBackend()
        async with Printer(backend) as p:
            await p.connect("fake")
            results = await asyncio.gather(*(p.print_id("123456789", "РОЗА") for _ in range(3)))
            self.assertEqual(backend.peak, 1)
            self.assertEqual(backend.sends, 3)
            self.assertTrue(all(r.status == "sent" and not r.physical_completion_confirmed for r in results))
        self.assertTrue(backend.disconnected)

    async def test_label_gate_and_config_atomicity(self):
        backend = FakeBackend()
        async with Printer(backend) as p:
            await p.connect("fake")
            with self.assertRaises(UnsupportedFeature):
                await p.configure_media(MediaConfig(paper_type=PaperType.LABELS))
            self.assertEqual(p.media.paper_type, PaperType.CONTINUOUS)
            with self.assertRaises(UnsupportedFeature): await p.calibrate()
        p = Printer(backend,media=MediaConfig(paper_type=PaperType.LABELS))
        async with p:
            await p.connect("fake")
            with self.assertRaises(UnsupportedFeature): await p.print_id("123456789","РОЗА")
        self.assertEqual(backend.sends,0)

    async def test_calibration_extension_and_gate(self):
        for enabled in (False,True):
            backend = FakeBackend(labels=True,calibration=enabled)
            async with Printer(backend,media=MediaConfig(paper_type=PaperType.LABELS)) as p:
                await p.connect("fake")
                if enabled: await p.calibrate()
                else:
                    with self.assertRaises(UnsupportedFeature): await p.calibrate()
            self.assertEqual(backend.calibrations,int(enabled))

    async def test_failed_send_not_retried(self):
        backend = FakeBackend(fail=True)
        async with Printer(backend) as p:
            await p.connect("fake")
            with self.assertRaises(PrintTransmissionError) as err: await p.print_id("123456789", "РОЗА")
            self.assertTrue(err.exception.may_have_printed)
            with self.assertRaises(ConnectionStateError): await p.print_id("123456789", "РОЗА")
            self.assertEqual(backend.sends,1)

    async def test_validation_can_be_corrected(self):
        backend = FakeBackend()
        async with Printer(backend) as p:
            await p.connect("fake")
            with self.assertRaises(ValueError): await p.print_id("bad", "РОЗА")
            await p.print_id("123456789", "РОЗА")
        self.assertEqual(backend.sends,1)

    async def test_cancel_requires_reconnect(self):
        started = asyncio.Event()
        class Waiting(FakeBackend):
            async def send(self, job):
                started.set()
                await asyncio.Event().wait()
        async with Printer(Waiting()) as p:
            await p.connect("fake")
            task = asyncio.create_task(p.print_id("123456789","РОЗА"))
            await started.wait()
            task.cancel()
            with self.assertRaises(asyncio.CancelledError): await task
            with self.assertRaises(ConnectionStateError): await p.print_id("123456789","РОЗА")


class SyncTests(unittest.TestCase):
    def test_same_loop_for_connection_and_jobs(self):
        backend = FakeBackend()
        with SyncPrinter(backend) as p:
            p.connect("fake")
            p.print_id("123456789","РОЗА")
            p.print_id(mode=LabelMode.BLANK_ID_NAME)
            p.print_id("123456789", mode=LabelMode.ID_ONLY)
            p.print_id(mode=LabelMode.BLANK_ID_ONLY)
        self.assertEqual(backend.sends,4)
        self.assertEqual(len(set(backend.loops)),1)
        self.assertTrue(backend.disconnected)
        with self.assertRaises(ConnectionStateError): p.connect("fake")

