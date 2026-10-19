import unittest
from unittest.mock import AsyncMock
from types import SimpleNamespace
from PIL import Image
from cyberwall_id_printer import LabelPrinter, LabelState, DeviceStatusError, LabelOperationError


class DeviceStatusTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.events = []
        self.printer = LabelPrinter(on_event=self.events.append)
        self.backend = SimpleNamespace(
            query_status=AsyncMock(return_value=(0, 0, 1.0)),
            status_reason=lambda status: 'normal' if status in (0, 128) else 'paper_out',
            print_image=AsyncMock(), move=AsyncMock(), position=AsyncMock(),
            check_connection=AsyncMock(return_value=True), disconnect=AsyncMock())
        self.printer._backend = self.backend
        self.printer.state = LabelState.READY

    async def test_paper_replacement_does_not_restore_position(self):
        self.backend.query_status.return_value = (1, 1, 1.0)
        with self.assertRaises(DeviceStatusError) as caught:
            await self.printer.poll()
        self.assertEqual(caught.exception.status, 1)
        self.assertEqual(self.printer.device_status, 'paper_out')
        self.assertEqual(self.printer.state, LabelState.UNKNOWN)
        self.backend.query_status.return_value = (0, 1, 2.0)
        await self.printer.poll()
        self.assertEqual(self.printer.device_status, 'normal')
        self.assertEqual(self.printer.state, LabelState.UNKNOWN)
        self.backend.position.assert_not_awaited()
        await self.printer.recover()
        self.assertEqual(self.printer.state, LabelState.READY)
        self.backend.print_image.assert_not_awaited()

    async def test_presentation_fault_keeps_cause_without_error_traceback(self):
        self.backend.move.side_effect = DeviceStatusError('paper_out', 1)
        with self.assertLogs('cyberwall_id_printer.label_printer', level='INFO') as logs:
            with self.assertRaises(LabelOperationError) as caught:
                await self.printer.print_image(Image.new('1', (384, 10), 1))
        error = caught.exception
        self.assertEqual((error.reason, error.status, error.stage), ('paper_out', 1, 'presenting'))
        self.assertTrue(error.may_have_printed)
        self.assertFalse(any(record.exc_info for record in logs.records))
        self.assertEqual(self.printer.device_status, 'paper_out')
        self.assertEqual(self.printer.state, LabelState.UNKNOWN)
        await self.printer.poll()
        self.assertEqual(self.printer.device_status, 'normal')
        self.assertIs(self.printer.last_error, error)
        self.backend.print_image.assert_awaited_once()
        self.backend.disconnect.assert_not_awaited()

    async def test_preflight_fault_does_not_print(self):
        self.backend.query_status.return_value = (1, 1, 1.0)
        with self.assertRaises(LabelOperationError) as caught:
            await self.printer.print_image(Image.new('1', (384, 10), 1))
        self.assertFalse(caught.exception.may_have_printed)
        self.backend.move.assert_not_awaited()
        self.backend.print_image.assert_not_awaited()

    async def test_disconnect_invalidates_device_status(self):
        self.printer.device_status = 'paper_out'
        await self.printer.disconnect()
        self.assertEqual(self.printer.device_status, 'unknown')
        self.assertEqual(self.events[-1]['device_status'], 'unknown')

    async def test_manual_motion_fault_is_structured(self):
        self.backend.move.side_effect = DeviceStatusError('paper_out', 1)
        with self.assertRaises(DeviceStatusError):
            await self.printer.move_paper(1)
        self.assertEqual(self.printer.device_status, 'paper_out')
        self.assertEqual(self.printer.state, LabelState.UNKNOWN)
