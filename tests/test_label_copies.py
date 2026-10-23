"""Label batches preserve positioning, waits, fault handling and session locking."""
import asyncio
import unittest
from unittest.mock import AsyncMock, patch
from PIL import Image
from cyberwall_id_printer import (
    LabelPrinter, LabelSettings, LabelState, LabelMode, RemovalRequired,
    LabelOperationError, DeviceStatusError,
)
from test_labels import FakeBackend


class LabelCopiesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        self.printer = LabelPrinter(on_event=self.events.append)
        self.backend = FakeBackend()
        self.printer._backend = self.backend
        await self.printer.connect('test')
        self.image = Image.new('1', (384, 240), 1)
        self.sleep = patch('cyberwall_id_printer.label_printer.asyncio.sleep', new=AsyncMock())
        self.sleep.start()
        self.addCleanup(self.sleep.stop)

    async def test_unknown_positions_once_and_presents_only_after_last_copy(self):
        result = await self.printer.print_copies(self.image, copies=3)
        self.assertEqual(self.backend.calls, [
            'F0', 'print', ('feed', 2), 'print', ('feed', 2), 'print', ('feed', 15)])
        self.assertEqual(self.printer.state, LabelState.PRESENTED)
        self.assertEqual((result.width_px, result.height_px, result.copies), (384, 240, 3))
        self.assertFalse(result.physical_completion_confirmed)
        self.assertEqual(sum(e['event'] == 'presented' for e in self.events), 1)
        self.assertFalse(self.printer._confirmed)

    async def test_ready_skips_positioning_and_honors_custom_gap_and_waits(self):
        self.printer.state = LabelState.READY
        self.printer.settings = LabelSettings(gap_mm=3, feed_mm=12,
            settle_seconds=4, motion_settle_seconds=6)
        calls = []
        async def raster(image, settle):
            calls.append(('print', settle))
            self.assertEqual(image.tobytes(), self.image.tobytes())
        async def move(mm, *, reverse, settle):
            calls.append(('move', mm, reverse, settle))
        self.backend.print_image, self.backend.move = raster, move
        await self.printer.print_copies(self.image, 2)
        self.assertEqual(calls, [('print', 4), ('move', 3, False, 6),
                                 ('print', 4), ('move', 12, False, 6)])
        self.assertEqual(self.backend.calls, [])

    async def test_presented_requires_confirmation_then_retracts_and_positions_once(self):
        await self.printer.print_image(self.image)
        self.backend.calls.clear()
        with self.assertRaises(RemovalRequired):
            await self.printer.print_copies(self.image, 2)
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.printer.state, LabelState.PRESENTED)
        await self.printer.confirm_removed()
        await self.printer.print_copies(self.image, 2)
        self.assertEqual(self.backend.calls, [
            ('reverse', 25), 'F0', 'print', ('feed', 2), 'print', ('feed', 15)])
        self.backend.calls.clear()
        with self.assertRaises(RemovalRequired):
            await self.printer.print_image(self.image)
        self.assertEqual(self.backend.calls, [])

    async def test_never_confirmation_also_applies_to_batches(self):
        self.printer.settings = LabelSettings(confirmation='never')
        self.printer.state = LabelState.PRESENTED
        await self.printer.print_copies(self.image, 2)
        self.assertEqual(self.backend.calls[:2], [('reverse', 25), 'F0'])

    async def test_one_copy_has_no_gap_motion(self):
        result = await self.printer.print_copies(self.image, 1)
        self.assertEqual(self.backend.calls, ['F0', 'print', ('feed', 15)])
        self.assertEqual(result.copies, 1)

    async def test_invalid_count_and_image_do_not_move_or_print(self):
        for copies in (0, -1, 101, True, 2.0, '2', None):
            with self.subTest(copies=copies), self.assertRaises(ValueError):
                await self.printer.print_copies(self.image, copies)
        for image in (Image.new('RGB', (384, 240)), Image.new('1', (320, 240)),
                      Image.new('1', (384, 241))):
            with self.assertRaises(ValueError):
                await self.printer.print_copies(image, 2)
        self.assertEqual(self.backend.calls, [])
        self.assertEqual(self.printer.state, LabelState.UNKNOWN)

    async def test_print_id_renders_batch_and_returns_count(self):
        result = await self.printer.print_id(mode=LabelMode.BLANK_ID_ONLY, copies=2)
        self.assertEqual(result.copies, 2)
        self.assertEqual(self.backend.calls, ['F0', 'print', ('feed', 2), 'print', ('feed', 15)])

    async def test_gap_failure_stops_series_without_presentation_or_replay(self):
        self.backend.fail = 'feed'
        with self.assertRaises(LabelOperationError) as caught:
            await self.printer.print_copies(self.image, 3)
        self.assertEqual(caught.exception.stage, 'advancing')
        self.assertTrue(caught.exception.may_have_printed)
        self.assertEqual(self.printer.state, LabelState.UNKNOWN)
        self.assertEqual(self.backend.calls, ['F0', 'print', ('feed', 2)])
        await self.printer.disconnect()
        await self.printer.connect('test')
        self.assertEqual(self.backend.calls[-1], 'disconnect')

    async def test_later_print_failure_and_final_feed_failure_stop_series(self):
        self.printer.state = LabelState.READY
        self.backend.print_image = AsyncMock(side_effect=[None, OSError('lost')])
        with self.assertRaises(LabelOperationError) as caught:
            await self.printer.print_copies(self.image, 3)
        self.assertEqual(caught.exception.stage, 'printing')
        self.assertTrue(caught.exception.may_have_printed)
        self.assertEqual(self.backend.print_image.await_count, 2)
        self.assertEqual(self.backend.calls, [('feed', 2)])
        self.assertEqual(self.printer.state, LabelState.UNKNOWN)

        self.printer.state = LabelState.READY
        self.backend.print_image = AsyncMock()
        self.backend.move = AsyncMock(side_effect=[None, OSError('feed failed')])
        with self.assertRaises(LabelOperationError) as caught:
            await self.printer.print_copies(self.image, 2)
        self.assertEqual(caught.exception.stage, 'presenting')
        self.assertTrue(caught.exception.may_have_printed)
        self.assertEqual(self.printer.state, LabelState.UNKNOWN)
        self.assertEqual(self.backend.print_image.await_count, 2)

    async def test_paper_out_or_epoch_change_between_copies_stops_next_print(self):
        for status in (0, 1):
            with self.subTest(status=status):
                self.printer.state = LabelState.READY
                self.printer._epoch = None
                self.backend.print_image = AsyncMock()
                self.backend.move = AsyncMock()
                self.backend.query_status = AsyncMock(side_effect=[(0, 0, 1), (status, 1, 2)])
                with self.assertRaises(LabelOperationError) as caught:
                    await self.printer.print_copies(self.image, 3)
                self.assertEqual(caught.exception.stage, 'advancing')
                self.assertTrue(caught.exception.may_have_printed)
                if status:
                    self.assertEqual(caught.exception.reason, 'paper_out')
                    self.assertIsInstance(caught.exception.__cause__, DeviceStatusError)
                self.backend.print_image.assert_awaited_once()
                self.assertEqual(self.printer.state, LabelState.UNKNOWN)

    async def test_cancel_during_gap_aborts_remaining_copies(self):
        self.printer.state = LabelState.READY
        self.backend.move = AsyncMock(side_effect=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.printer.print_copies(self.image, 3)
        self.assertEqual(self.backend.calls, ['print'])
        self.assertEqual(self.printer.state, LabelState.UNKNOWN)

    async def test_batch_holds_lock_until_presentation_and_awaits_each_operation(self):
        self.printer.state = LabelState.READY
        started, finish_print = asyncio.Event(), asyncio.Event()
        async def raster(image, settle):
            self.backend.calls.append('print start')
            started.set()
            await finish_print.wait()
            self.backend.calls.append('print done')
        self.backend.print_image = raster
        task = asyncio.create_task(self.printer.print_copies(self.image, 2))
        await started.wait()
        disconnect = asyncio.create_task(self.printer.disconnect())
        self.assertEqual(self.backend.calls, ['print start'])
        self.assertFalse(disconnect.done())
        finish_print.set()
        await task
        await disconnect
        self.assertEqual(self.backend.calls, [
            'print start', 'print done', ('feed', 2),
            'print start', 'print done', ('feed', 15), 'disconnect'])

    def test_gap_validation(self):
        self.assertEqual(LabelSettings().gap_mm, 2)
        for gap in (0, -1, 26, True, float('nan'), float('inf')):
            with self.subTest(gap=gap), self.assertRaises(ValueError):
                LabelSettings(gap_mm=gap)
        for gap in (0.25, 25):
            self.assertEqual(LabelSettings(gap_mm=gap).gap_mm, gap)
