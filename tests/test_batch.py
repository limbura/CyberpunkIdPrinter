import asyncio
import unittest
from unittest.mock import AsyncMock
from cyberwall_id_printer import Printer, LabelMode, PrintTransmissionError, ConnectionStateError
from test_core import FakeBackend


class BatchBackend(FakeBackend):
    def prepare_copies(self, image, media, copies):
        self.copies = copies
        return image

    async def check_connection(self):
        return not self.disconnected


class BatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_sent_once_and_connection_reused(self):
        backend = BatchBackend()
        async with Printer(backend) as printer:
            await printer.connect('fake')
            result = await printer.print_id(mode=LabelMode.BLANK_ID_NAME, copies=10)
            self.assertEqual((result.copies, backend.sends, backend.copies), (10, 1, 10))
            self.assertFalse(result.physical_completion_confirmed)
            await printer.print_id('123456789', mode=LabelMode.ID_ONLY)
            self.assertEqual(backend.sends, 2)

    async def test_invalid_batch_never_sends(self):
        backend = BatchBackend()
        async with Printer(backend) as printer:
            await printer.connect('fake')
            for copies in (0, -1, True, 1.5, '2', 101):
                with self.assertRaises(ValueError):
                    await printer.print_id(mode=LabelMode.BLANK_ID_ONLY, copies=copies)
            self.assertEqual(backend.sends, 0)

    async def test_failed_batch_has_no_replay(self):
        backend = BatchBackend(fail=True)
        async with Printer(backend) as printer:
            await printer.connect('fake')
            with self.assertRaises(PrintTransmissionError):
                await printer.print_id(mode=LabelMode.BLANK_ID_ONLY, copies=10)
            with self.assertRaises(ConnectionStateError):
                await printer.print_id(mode=LabelMode.BLANK_ID_ONLY, copies=10)
            self.assertEqual(backend.sends, 1)

    async def test_idle_disconnect_invalidates_session(self):
        backend = BatchBackend()
        async with Printer(backend) as printer:
            self.assertFalse(await printer.check_connection())
            await printer.connect('fake')
            backend.disconnected = False
            self.assertTrue(await printer.check_connection())
            backend.disconnected = True
            self.assertFalse(await printer.check_connection())
            with self.assertRaises(ConnectionStateError):
                await printer.print_id(mode=LabelMode.BLANK_ID_ONLY)
