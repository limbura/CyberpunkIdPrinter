"""Thin adapter to a separate, revision-pinned TiMini Print checkout.

No upstream code is bundled. Imports and Bluetooth are lazy; rendering works
without TiMini or Bluetooth dependencies. Only V5G and V5X simple roll
recipes are enabled in v0.1; other families need their own layout validation.
"""
from __future__ import annotations
import importlib
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
from PIL import Image
from ..errors import BackendUnavailable, UnsupportedFeature
from ..models import MediaConfig, PaperType, PrinterCapabilities, validate_copies

logging.getLogger(__name__).addHandler(logging.NullHandler())

TIMINI_REVISION = "679fc0650e61c7ab28b44542b9b9ba000e45aa78"
TIMINI_URL = "https://github.com/Dejniel/TiMini-Print.git"


def load_timini(path: str | Path | None = None) -> Path:
    if getattr(sys, "frozen", False):
        # Build-time Git validation is recorded in a bundled manifest. Never use
        # environment paths or external source trees in a standalone executable.
        root = Path(sys._MEIPASS).resolve()
        try:
            manifest = json.loads((root / "cyberwall_timini.json").read_text(encoding="utf-8"))
            if manifest["revision"] != TIMINI_REVISION:
                raise ValueError("TiMini revision mismatch")
            module = importlib.import_module("timiniprint")
            if Path(module.__file__).resolve().parent != root / "timiniprint":
                raise ValueError("TiMini was loaded outside the executable bundle")
            importlib.import_module("timiniprint.devices")
        except (OSError, ImportError, ValueError, KeyError, TypeError) as exc:
            raise BackendUnavailable("Bundled TiMini is missing or incompatible; rebuild the complete application") from exc
        return root
    configured = path or os.environ.get("CYBERWALL_TIMINI_PATH")
    if not configured:
        raise BackendUnavailable("Set CYBERWALL_TIMINI_PATH to the external TiMini Print checkout (see README)")
    root = Path(configured).expanduser().resolve()
    if not (root / "timiniprint" / "__init__.py").is_file():
        raise BackendUnavailable(f"Not a TiMini Print checkout: {root}")
    try:
        revision = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"],
                                           text=True, stderr=subprocess.PIPE).strip()
        dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--untracked-files=normal"],
                                        text=True, stderr=subprocess.PIPE).strip()
    except (OSError, subprocess.CalledProcessError) as exc:
        raise BackendUnavailable("Git is required to verify the external TiMini checkout") from exc
    if revision != TIMINI_REVISION or dirty:
        raise BackendUnavailable(f"TiMini must be a clean checkout of {TIMINI_REVISION}; found {revision}")
    loaded = sys.modules.get("timiniprint")
    if loaded is not None and Path(loaded.__file__).resolve().parent != root / "timiniprint":
        raise BackendUnavailable("Another TiMini checkout is already imported; use a fresh process")
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        importlib.import_module("timiniprint.devices")
    except ImportError as exc:
        raise BackendUnavailable('Install cyberwall_id_printer[printer] in this environment') from exc
    return root


def _imports(path):
    load_timini(path)
    try:
        from timiniprint.devices import PrinterCatalog
        from timiniprint.transport.bluetooth import BluetoothDiscovery, BleakBluetoothConnector, DeviceTransport
        from timiniprint.printing.connected import connect_printer
        return PrinterCatalog, BluetoothDiscovery, BleakBluetoothConnector, DeviceTransport, connect_printer
    except ImportError as exc:
        raise BackendUnavailable('Install cyberwall_id_printer[printer] in this environment') from exc


async def _scan_ble_devices(discovery, timeout):
    """Scan on the caller's loop, then let TiMini resolve the model catalog.

    TiMini's async scan_devices() delegates to a blocking worker which creates
    and immediately closes its own event loop. A late WinRT scanner callback
    can then target that closed loop. Await Bleak directly instead; the caller
    owns the loop and cancellation also waits for Bleak's scanner cleanup.
    """
    from bleak import BleakScanner
    from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport

    advertisements = await BleakScanner.discover(timeout=timeout, return_adv=True)
    endpoints, ble_devices = [], {}
    for device, advertisement in advertisements.values():
        ble_devices[device.address.casefold()] = device
        endpoints.append(DeviceInfo(
            name=advertisement.local_name or device.name or "",
            address=device.address,
            paired=None,
            transport=DeviceTransport.BLE,
            manufacturer_data=tuple(
                company.to_bytes(2, "little") + bytes(value)
                for company, value in advertisement.manufacturer_data.items()
            ),
        ))
    return discovery.devices_from_scan(endpoints), ble_devices


def capabilities_for(device) -> PrinterCapabilities:
    family = device.protocol_family.value
    if family not in ("v5g", "v5x"):
        raise UnsupportedFeature(f"Profile {device.profile_key}: family {family} is not enabled in v0.1")
    presets = device.profile.paper_presets
    plain = next((p for p in presets if p.paper_mode is None or p.paper_mode.value == "plain"), None)
    if plain is None:
        raise UnsupportedFeature("No continuous roll preset available")
    labels = family == "v5x" and any(p.paper_mode and p.paper_mode.value == "tag" for p in presets)
    return PrinterCapabilities(device.name, device.address, device.profile_key,
        plain.paper_width_px, device.profile.dev_dpi, hardware_labels=labels,
        label_note=("V5X TAG recipe from TiMini; physical label positioning needs hardware testing."
                    if labels else "Hardware label positioning is not implemented for this thermal printer profile."))


def encode_job(device, runtime_capabilities, image: Image.Image, media: MediaConfig, blackening: int = 3,
               *, page_index: int = 1, page_count: int = 1):
    from timiniprint.protocol.job import PrinterProtocol
    from timiniprint.raster import RasterBuffer, RasterSet, PixelFormat
    caps = capabilities_for(device)
    if media.paper_type == PaperType.LABELS and not caps.hardware_labels:
        raise UnsupportedFeature(caps.label_note)
    mode = "tag" if media.paper_type == PaperType.LABELS else "plain"
    preset = next(p for p in device.profile.paper_presets
                  if (p.paper_mode.value if p.paper_mode else "plain") == mode)
    if (preset.mirror_horizontal or preset.rotation_degrees or preset.top_padding_px
            or preset.left_padding_px or preset.raster_height_px is not None):
        raise UnsupportedFeature("This preset requires a dedicated media layout adapter")
    if image.mode != "1" or image.width != preset.paper_width_px:
        raise ValueError("Expected a native monochrome raster padded to the preset head width")
    if preset.max_height_px is not None and image.height > preset.max_height_px:
        raise ValueError("Design exceeds preset maximum height")
    # Pillow: zero = black. TiMini BW1: one = black. No grayscale or dithering.
    raster = RasterBuffer([int(pixel == 0) for pixel in image.convert("L").tobytes()], image.width, PixelFormat.BW1)
    return PrinterProtocol(device).build_job(RasterSet.from_single(raster),
        is_text=False, blackening=blackening, paper_preset_key=preset.key,
        pixel_format_override=PixelFormat.BW1, runtime_capabilities=runtime_capabilities,
        page_index=page_index, page_count=page_count)


class TiMiniBackend:
    def __init__(self, path: str | Path | None = None, *, blackening: int = 3,
                 timeout: float = 20.0, connect_attempts: int = 2, scan_timeout: float = 15.0):
        from math import isfinite
        if isinstance(blackening, bool) or not isinstance(blackening, int) or not 1 <= blackening <= 5:
            raise ValueError("blackening must be an integer from 1 to 5")
        if not isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if type(connect_attempts) is not int or connect_attempts < 1:
            raise ValueError("connect_attempts must be a positive integer")
        if not isfinite(scan_timeout) or scan_timeout <= 0:
            raise ValueError("scan_timeout must be finite and positive")
        self.connect_attempts, self.scan_timeout = connect_attempts, scan_timeout
        self.path, self.blackening, self.timeout = path, blackening, timeout
        self._connected = None

    async def scan(self) -> list[dict[str, str]]:
        Catalog, Discovery, _, _, _ = _imports(self.path)
        devices, _ = await _scan_ble_devices(Discovery(Catalog.load()), self.scan_timeout)
        return [{"name": d.name, "address": d.address, "profile": d.profile_key} for d in devices]

    async def connect(self, address: str) -> PrinterCapabilities:
        import asyncio
        if self._connected is not None:
            raise RuntimeError("Backend is already connected")
        for attempt in range(self.connect_attempts):
            try:
                logging.getLogger(__name__).info("Connection attempt %s/%s", attempt + 1, self.connect_attempts)
                return await self._connect_once(address)
            except (BackendUnavailable, UnsupportedFeature, ValueError):
                raise
            except Exception:
                logging.getLogger(__name__).exception("Connection attempt failed")
                await self.disconnect()
                if attempt + 1 == self.connect_attempts:
                    raise
                await asyncio.sleep(1)

    def _connection_options(self, device):
        return {}

    async def _connect_once(self, address: str) -> PrinterCapabilities:
        if self._connected is not None:
            raise RuntimeError("Backend is already connected")
        Catalog, Discovery, Connector, _, connect = _imports(self.path)
        from timiniprint.devices import BluetoothEndpointResolver
        catalog = Catalog.load()
        # Upstream resolve_device() hardcodes a five-second discovery window.
        # Use the configured timeout here as well as in standalone scan().
        discovery = Discovery(catalog)
        devices = []
        # BLE advertisements often arrive without a name first. Wait for the
        # advertisement carrying the model name, not merely the MAC address.
        from timiniprint.transport.bluetooth.types import DeviceInfo, DeviceTransport
        from bleak import BleakScanner
        import time
        started = time.monotonic()
        found = None
        target_name = None
        ble_devices = {}

        def target_with_name(device, advertisement):
            nonlocal target_name
            if device.address.casefold() != address.casefold():
                return False
            target_name = advertisement.local_name or device.name or ""
            return bool(target_name)

        try:
            found = await BleakScanner.find_device_by_filter(
                target_with_name, timeout=self.scan_timeout)
            if found is not None:
                endpoint = DeviceInfo(
                    name=target_name or found.name or "", address=found.address,
                    paired=False, transport=DeviceTransport.BLE,
                )
                devices = discovery.devices_from_scan([endpoint])
                logging.getLogger(__name__).info(
                    "Targeted BLE discovery %s name=%r elapsed=%.2fs resolved=%s",
                    address, endpoint.name, time.monotonic() - started, len(devices))
        except (OSError, RuntimeError) as exc:
            logging.getLogger(__name__).warning(
                "Targeted BLE discovery failed (%s); using regular discovery", exc)
        if not devices:
            # Some platforms omit names from advertisements, preventing the
            # model catalog from resolving a single endpoint. Run a full scan
            # on this same loop, preserving manufacturer data for resolution.
            devices, ble_devices = await _scan_ble_devices(discovery, self.scan_timeout)
        if not devices:
            raise RuntimeError("No supported printers found")
        device = BluetoothEndpointResolver(catalog).select_device(devices, address)
        if device is None:
            raise RuntimeError(f"No device matches '{address}'")
        if ble_devices:
            found = ble_devices.get(device.address.casefold())
        capabilities_for(device)  # Refuse unsupported families before device initialization.
        from .ble_connector import connector_with_timeout
        options = self._connection_options(device)
        # Reuse either scan's Windows BLEDevice in TiMini's private connection
        # loop to avoid another scan there; never reuse it on macOS.
        import sys
        cached = found if found is not None and devices and sys.platform == "win32" else None
        connector = connector_with_timeout(self.timeout, ble_device=cached)
        connected = await connect(device, connector, timeout=self.timeout, **options)
        try:
            capabilities = capabilities_for(connected.printer_device())
        except BaseException:
            await connected.disconnect()
            raise
        self._connected = connected
        return capabilities

    async def disconnect(self) -> None:
        connected, self._connected = self._connected, None
        if connected is not None:
            await connected.disconnect()

    def prepare(self, image: Image.Image, media: MediaConfig):
        if self._connected is None:
            raise RuntimeError("Backend is not connected")
        return encode_job(self._connected.printer_device(), self._connected.print_capabilities(),
                          image, media, self.blackening)

    def prepare_copies(self, image: Image.Image, media: MediaConfig, copies: int):
        validate_copies(copies)
        if self._connected is None:
            raise RuntimeError("Backend is not connected")
        connected = self._connected
        return connected.raster_pages_job(
            encode_job(connected.printer_device(), connected.print_capabilities(),
                       image, media, self.blackening, page_index=i, page_count=copies)
            for i in range(1, copies + 1)
        )

    async def check_connection(self) -> bool:
        if self._connected is None:
            return False
        # Pinned upstream has no public liveness API on ConnectedPrinter.
        # Its BLE loop runs inside SppBackend's serialized worker, only during
        # operations. Pump it there so idle disconnect notifications are handled;
        # never run that loop or touch GATT from our application's thread.
        import asyncio
        backend = self._connected._connection._backend

        def check():
            sock = backend._sock
            if sock is None or not backend.is_connected():
                return False
            client, loop = sock._client, sock._loop
            if client is None or loop is None or loop.is_closed():
                return False
            loop.run_until_complete(asyncio.sleep(0.05))
            return bool(client.is_connected)

        return await backend._run_blocking(check)

    async def send(self, job) -> None:
        if self._connected is None:
            raise RuntimeError("Backend is not connected")
        await self._connected.send_job(job, timeout=self.timeout)

    async def calibrate(self, media: MediaConfig) -> None:
        raise UnsupportedFeature("No verified calibration command is implemented for this backend")
