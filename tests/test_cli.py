import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from PIL import Image
from cyberwall_id_printer.cli import main


class CliTests(unittest.TestCase):
    def test_render_all_modes(self):
        cases = [(['--id','123456789','--name','Роза фон Праунхайм']),
                 ['--mode','blank-id-name'],
                 ['--mode','id-only','--id','001002003'],
                 ['--mode','blank-id-only']]
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            for i,args in enumerate(cases):
                output=Path(tmp)/f'{i}.png'
                self.assertEqual(main(['render',*args,'--output',str(output)]),0)
                with Image.open(output) as im:
                    self.assertEqual((im.mode,im.size),('1',(320,240)))

    def test_invalid_print_never_connects(self):
        cases = [[], ['--mode','id-only'],
                 ['--mode','blank-id-only','--id','123456789'],
                 ['--mode','blank-id-name','--no-inactive'],
                 ['--id','123456789','--name','А Б В Г']]
        with patch('cyberwall_id_printer.cli.TiMiniBackend') as backend, contextlib.redirect_stderr(io.StringIO()):
            for args in cases:
                self.assertEqual(main(['print','--address','fake',*args]),2)
            backend.assert_not_called()
