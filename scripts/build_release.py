"""Create a verified source release ZIP for cyberwall_id_printer.

The ZIP is an installable Python source tree (pyproject.toml included). It
excludes tests, CI files, tooling experiments, local outputs, and the external
TiMini-Print dependency. Only Python's standard library is used.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
from pathlib import Path
import re
from zipfile import ZIP_DEFLATED, ZipFile


ROOT = Path(__file__).resolve().parents[1]
VERSION_FILE = "src/cyberwall_id_printer/__init__.py"
TAG_PATTERN = re.compile(r"v(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)\Z")
BASE_FILES = ("README.md", "pyproject.toml", "MANIFEST.in")


def library_version(root: Path) -> str:
    """Extract version without importing Pillow, Bleak, or the printer backend."""
    version_path = root / VERSION_FILE
    parsed = ast.parse(version_path.read_text(encoding="utf-8"), filename=str(version_path))
    for node in parsed.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "__version__"
            for target in node.targets
        ):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                return node.value.value
    raise ValueError("В библиотеке отсутствует строковая константа __version__")


def included_files(root: Path) -> tuple[str, ...]:
    """Explicitly select distributable files; never include caches or test outputs."""
    files = set(BASE_FILES)
    for subdir, extensions in (
        ("src/cyberwall_id_printer", {".py", ".typed"}),
        ("examples", {".py"}),
        ("docs", {".md", ".png"}),
    ):
        directory = root / subdir
        if not directory.is_dir():
            raise FileNotFoundError(f"Отсутствует каталог {directory}")
        for path in directory.rglob("*"):
            if path.is_file() and path.suffix in extensions:
                files.add(path.relative_to(root).as_posix())
    missing = [path for path in files if not (root / path).is_file()]
    if missing or VERSION_FILE not in files:
        raise FileNotFoundError(f"Недостающие файлы релиза: {missing or [VERSION_FILE]}")
    return tuple(sorted(files))


def build_archive(
    tag: str,
    repository_root: Path = ROOT,
    output_dir: Path | None = None,
) -> tuple[Path, Path]:
    """Validate exact tag, package selected sources, and produce SHA-256 checksum."""
    if not TAG_PATTERN.fullmatch(tag):
        raise ValueError(f"Неверный тег {tag!r}: требуется vX.Y.Z")

    root = Path(repository_root).resolve()
    version = library_version(root)
    if tag != "v" + version:
        raise ValueError(f"Тег {tag} не соответствует __version__={version}")

    files = included_files(root)
    destination = Path(output_dir).resolve() if output_dir else root / "dist"
    destination.mkdir(parents=True, exist_ok=True)
    package_name = f"cyberwall_id_printer_{tag}"
    archive_path = destination / f"{package_name}.zip"
    checksum_path = destination / f"{package_name}.sha256"

    expected = [f"{package_name}/{file}" for file in files]
    with ZipFile(archive_path, "w", compression=ZIP_DEFLATED, compresslevel=9) as archive:
        for file in files:
            archive.write(root / file, arcname=f"{package_name}/{file}")

    with ZipFile(archive_path, "r") as archive:
        if archive.namelist() != expected or archive.testzip() is not None:
            raise ValueError("Проверка целостности или состава ZIP не пройдена")

    digest = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    checksum_path.write_text(f"{digest}  {archive_path.name}\n", encoding="ascii")
    return archive_path, checksum_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tag", required=True, help="Тег vX.Y.Z, совпадающий с __version__")
    parser.add_argument("--output-dir", type=Path, default=Path("dist"))
    args = parser.parse_args()
    archive, checksum = build_archive(args.tag, output_dir=args.output_dir)
    print(f"Архив: {archive}")
    print(f"Размер: {archive.stat().st_size} байт")
    print(f"Контрольная сумма: {checksum.read_text(encoding='ascii').strip()}")


if __name__ == "__main__":
    main()
