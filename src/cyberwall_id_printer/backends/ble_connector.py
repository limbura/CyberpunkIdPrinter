"""Compatibility boundary for the pinned TiMini BLE connection timeout.

No global monkey-patches: each connection owns its socket and timeout.
Imports are lazy so rendering still needs only Pillow.
"""
def connector_with_timeout(timeout, *, ble_device=None):
    from timiniprint import reporting
    from timiniprint.transport.bluetooth.backend import SppBackend
    from timiniprint.transport.bluetooth.connector import BleakBluetoothConnector, BleakBluetoothConnection
    from timiniprint.transport.bluetooth.adapters.bleak_adapter import _BleakSocket
    from timiniprint.transport.bluetooth.types import DeviceTransport
    from timiniprint.devices import bluetooth_connection_plan

    class Backend(SppBackend):
        def _connect_with_device(self, device, pairing_hint):
            if device.transport != DeviceTransport.BLE:
                raise ValueError("This connector supports BLE only")
            sock = _BleakSocket(pairing_hint=pairing_hint, ble_profile=device.ble_profile,
                                ble_mtu_request=device.ble_mtu_request, reporter=self._reporter,
                                device_cache=({device.address.upper(): ble_device} if ble_device is not None else {}))
            sock.settimeout(timeout)
            try:
                sock.connect((device.address, 1))
            except BaseException:
                sock.close()
                raise
            self._sock, self._connected = sock, True
            self._channel, self._transport = 1, DeviceTransport.BLE

    class Connector(BleakBluetoothConnector):
        async def connect(self, device):
            plan = bluetooth_connection_plan(device)
            backend = Backend()
            try:
                await backend.connect_attempts(
                    [self._to_device_info(a.endpoint, device) for a in plan.attempts],
                    pairing_hint=plan.pairing_hint)
            except BaseException:
                await backend.disconnect()
                raise
            return BleakBluetoothConnection(backend, device, reporting.DUMMY_REPORTER)

    return Connector()
