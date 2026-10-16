import json
import subprocess
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from cyberwall_id_printer.backends.timini import load_timini, TIMINI_REVISION
from cyberwall_id_printer import BackendUnavailable


class FrozenLoaderTests(unittest.TestCase):
    def test_frozen_ignores_external_paths_and_never_requires_git(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp).resolve()
            (root/'cyberwall_timini.json').write_text(json.dumps({'revision': TIMINI_REVISION}))
            module = SimpleNamespace(__file__=str(root/'timiniprint/__init__.py'))
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', tmp, create=True), \
                 patch('importlib.import_module', return_value=module), \
                 patch.object(subprocess, 'check_output', side_effect=AssertionError('Git must not run')):
                self.assertEqual(load_timini('C:/missing/external/checkout'), root)
                (root/'cyberwall_timini.json').write_text('{"revision":"wrong"}')
                with self.assertRaises(BackendUnavailable): load_timini()
                (root/'cyberwall_timini.json').unlink()
                with self.assertRaises(BackendUnavailable): load_timini()

    def test_frozen_refuses_external_module(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp)/'cyberwall_timini.json').write_text(json.dumps({'revision':TIMINI_REVISION}))
            with patch.object(sys, 'frozen', True, create=True), patch.object(sys, '_MEIPASS', tmp, create=True), \
                 patch('importlib.import_module', return_value=SimpleNamespace(__file__='/elsewhere/timiniprint/__init__.py')):
                with self.assertRaises(BackendUnavailable): load_timini()
