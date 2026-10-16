from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import sys
from . import __version__
from .backends.timini import TiMiniBackend, load_timini
from .errors import PrinterError
from .models import MediaConfig, PaperType, validate_copies
from .printer import Printer
from .renderer import LabelConfig, LabelMode, render_label


def _geometry(parser):
    parser.add_argument("--width-mm", type=float, default=40)
    parser.add_argument("--height-mm", type=float, default=30)
    parser.add_argument("--dpi", type=float, default=203.2)


def _content(parser):
    parser.add_argument("--mode", choices=[m.value for m in LabelMode], default=LabelMode.ID_NAME.value)
    parser.add_argument("--id", dest="account_id")
    parser.add_argument("--name")
    parser.add_argument("--no-inactive", action="store_true")
    parser.add_argument("--no-decorations", action="store_true")


def _label_config(args):
    return LabelConfig(args.width_mm, args.height_mm, args.dpi,
                       not args.no_inactive, not args.no_decorations, LabelMode(args.mode))


def _media(args):
    return MediaConfig(PaperType(args.paper), args.width_mm, args.height_mm,
                       args.roll_width_mm, args.offset_x_mm, args.dpi)


async def _device_command(args):
    backend = TiMiniBackend(args.timini_path, blackening=args.blackening)
    if args.command == "scan":
        return await backend.scan()
    async with Printer(backend, media=_media(args)) as printer:
        caps = await printer.connect(args.address)
        if args.command == "info":
            return asdict(caps)
        if args.command == "calibrate":
            await printer.calibrate()
            return {"status": "calibrated"}
        return asdict(await printer.print_id(args.account_id, args.name,
                      mode=LabelMode(args.mode), inactive=not args.no_inactive,
                      decorations=not args.no_decorations, copies=args.copies))


def main(argv=None):
    parser = argparse.ArgumentParser(description="CYBERWALL segmented labels and Bluetooth printing")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    render = sub.add_parser("render", help="Create a native 1-bit PNG; no printer needed")
    _geometry(render)
    _content(render)
    render.add_argument("--output", type=Path, required=True)
    render.add_argument("--preview", type=Path, help="Optional 4x preview, not for printing")
    check = sub.add_parser("check-backend", help="Verify external checkout and dependencies without Bluetooth")
    check.add_argument("--timini-path", type=Path)
    for command in ("scan", "info", "print", "calibrate"):
        p = sub.add_parser(command)
        p.add_argument("--timini-path", type=Path)
        p.add_argument("--blackening", type=int, choices=range(1,6), default=3)
        if command != "scan":
            p.add_argument("--address", required=True)
            _geometry(p)
            p.add_argument("--roll-width-mm", type=float, default=57)
            p.add_argument("--offset-x-mm", type=float, default=0)
            p.add_argument("--paper", choices=[p.value for p in PaperType], default="labels" if command == "calibrate" else "continuous")
        if command == "print":
            _content(p)
            p.add_argument("--copies", type=int, default=1, help="Copies in one job (1–100)")
    args = parser.parse_args(argv)
    try:
        if args.command == "print":
            validate_copies(args.copies)
            # Reject bad/ignored content before any Bluetooth connection or paper motion.
            render_label(args.account_id, args.name, _label_config(args))
        if args.command == "render":
            image = render_label(args.account_id, args.name, _label_config(args))
            args.output.parent.mkdir(parents=True, exist_ok=True)
            image.save(args.output, dpi=(args.dpi,args.dpi))
            if args.preview:
                from PIL import Image
                args.preview.parent.mkdir(parents=True, exist_ok=True)
                image.resize((image.width*4, image.height*4), Image.Resampling.NEAREST).save(args.preview)
            result = {"output": str(args.output), "width_px": image.width, "height_px": image.height}
        elif args.command == "check-backend":
            from .backends.timini import _imports, TIMINI_REVISION
            root = load_timini(args.timini_path)
            _imports(root)
            result = {"path": str(root), "revision": TIMINI_REVISION, "status": "ready"}
        else:
            result = asyncio.run(_device_command(args))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (PrinterError, ValueError, OSError, RuntimeError) as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrupted. If transmission had started, inspect the paper before retrying.", file=sys.stderr)
        return 130
