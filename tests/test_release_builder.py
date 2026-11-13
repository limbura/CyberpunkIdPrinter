"""Unit tests for GitHub release source packaging (no physical printer required)."""

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from zipfile import ZipFile


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("build_printer_release", ROOT / "scripts" / "build_release.py")
release = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(release)


class ReleasePackagingTests(unittest.TestCase):
    def test_source_archive_contains_installable_package_and_documentation(self):
        tag = "v" + release.library_version(ROOT)
        with tempfile.TemporaryDirectory() as temporary:
            archive_path, checksum_path = release.build_archive(
                tag, repository_root=ROOT, output_dir=Path(temporary)
            )
            self.assertEqual(archive_path.name, f"cyberwall_id_printer_{tag}.zip")
            self.assertTrue(checksum_path.is_file())
            with ZipFile(archive_path) as archive:
                self.assertIsNone(archive.testzip())
                root_dir = f"cyberwall_id_printer_{tag}/"
                expected = [root_dir + name for name in release.included_files(ROOT)]
                self.assertEqual(archive.namelist(), expected)
                for path in (
                    "README.md",
                    "pyproject.toml",
                    "src/cyberwall_id_printer/__init__.py",
                    "src/cyberwall_id_printer/backends/timini.py",
                    "src/cyberwall_id_printer/py.typed",
                    "examples/print_async.py",
                    "docs/label_preview.png",
                ):
                    self.assertIn(root_dir + path, archive.namelist())
                self.assertNotIn("/tests/", "\n".join(archive.namelist()))
                self.assertNotIn("/other/", "\n".join(archive.namelist()))
                self.assertNotIn("/.github/", "\n".join(archive.namelist()))
            checksum = hashlib.sha256(archive_path.read_bytes()).hexdigest()
            self.assertEqual(
                checksum_path.read_text(encoding="ascii"),
                f"{checksum}  {archive_path.name}\n",
            )

    def test_tag_must_be_stable_semver_matching_source_version(self):
        with tempfile.TemporaryDirectory() as temporary:
            out = Path(temporary)
            for invalid in (
                "v1.3", "v1.3.0.1", "v1.3.0-rc1", "v01.3.0",
                "1.3.0", "v9.9.9", "v1.3.0;echo bad", "",
            ):
                with self.subTest(tag=invalid), self.assertRaises(ValueError):
                    release.build_archive(invalid, repository_root=ROOT, output_dir=out)
            self.assertEqual(list(out.iterdir()), [])

    def test_missing_required_file_refuses_incomplete_release(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / "checkout"
            version_file = root / release.VERSION_FILE
            version_file.parent.mkdir(parents=True)
            version_file.write_text('__version__ = "1.3.0"\n', encoding="utf-8")
            with self.assertRaises(FileNotFoundError):
                release.build_archive(
                    "v1.3.0", repository_root=root,
                    output_dir=Path(temporary) / "dist"
                )
            self.assertFalse((Path(temporary) / "dist").exists())

    def test_version_extraction_does_not_import_hardware_dependencies(self):
        self.assertRegex(release.library_version(ROOT), r"^\d+\.\d+\.\d+$")


if __name__ == "__main__":
    unittest.main()
