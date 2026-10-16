"""
Этот скрипт работает с принтером без TiMini библиотеки непосредственно отправляя команды по Bluetooth.
Для отладки и тестирования принтера.

Interactive V5G label printer experiment, based on a working FunPrint capture.

Connect once and choose operations repeatedly from the console:
  1 - send F0 positioning command
  2 - print one X (40 x 30 mm label)
  3 <mm> - advance paper (experimental A1)
  4 <mm> - retract paper (experimental A0)
  0 - disconnect and exit

No automatic F0, no retries, no automatic extra paper feed.
Measured for A1: 1 payload unit moves paper about 0.25 mm (4 units/mm).
Assume the same scale for A0 retract until independently measured.
Observed A1 motion saturates around 25 mm; exact firmware limit is unverified.
Requires: pip install bleak
Self-check without Bluetooth: python thermoprinter_label_test.py --self-test

F0 parameters and print-control packets are preserved from the working capture;
they are NOT a general calibration API. Raster geometry assumes 203 dpi (~8 px/mm);
paper-motion A0/A1 units are NOT raster dots.
"""
import argparse
import asyncio
import math
from datetime import datetime
from pathlib import Path
import time

# Parameters for the current 40 x 30 mm labels (width across the tape x feed length).
PRINT_WIDTH_PX = 384            # Printhead raster width: always 48 bytes per A2 row.
LABEL_WIDTH_PX = 320            # 40 mm across the paper at 203 dpi (approximately).
LABEL_LENGTH_PX = 240           # 30 mm along the paper at 203 dpi (approximately).
X_OFFSET_PX = 0                 # Paper is now mechanically aligned left.
LINE_WIDTH_PX = 2              # Diagonal line thickness (pixels).
MOTION_UNITS_PER_MM = 4         # Measured A1: ~0.25 mm / protocol unit (NOT printhead dots).
MAX_MOTION_MM = 25.0            # Measured A1 plateau at ~25 mm, likely firmware cap.
# The plateau above ~100 A1 units is a working hypothesis, not a proven hard limit.
# A0 retract is assumed to use the same 4 units/mm and ~25 mm limit, UNTESTED.
# WARNING: reversing too far can peel an adhesive label and jam the printer.

WRITE = '0000ae01-0000-1000-8000-00805f9b34fb'
NOTIFY = '0000ae02-0000-1000-8000-00805f9b34fb'
F0 = bytes.fromhex('5178f000030000832069ff')


def crc8(data):
    crc = 0
    for byte in data:
        crc ^= byte
        for _ in range(8):
            crc = ((crc << 1) ^ 7) & 255 if crc & 128 else (crc << 1) & 255
    return crc


def packet(op, data, response=False):
    return (b'\x51\x78' + bytes((op, int(response))) +
            len(data).to_bytes(2, 'little') + data + bytes((crc8(data), 255)))


def motion_packet(op, distance_mm):
    """Build A1 forward / A0 backward motion packet for a requested distance.

    Experimental A1 measurements: the actual paper travel was approximately
    2 mm per 1 mm previously requested when using 203/25.4 dots/mm; therefore
    the two-byte little-endian payload corresponds to ~0.25 mm per unit.
    We now encode N = round(distance_mm * 4), NOT 203/25.4.

    A1 movement stopped increasing above about 25 mm (~100 payload units),
    possibly due to firmware clamping. This plateau needs no further reliance:
    we cap user input at 25 mm rather than relying on the printer to clamp it.
    A0 is provisionally assumed to use the SAME scale and cap; NOT measured.
    Long A0 reverse moves can detach labels inside the printer and cause jams.
    """
    if op not in (0xA0, 0xA1):
        raise ValueError('Motion opcode must be A0 or A1')
    if not math.isfinite(distance_mm) or not (0 < distance_mm <= MAX_MOTION_MM):
        raise ValueError(f'Distance must be > 0 and <= {MAX_MOTION_MM:g} mm')
    units = round(distance_mm * MOTION_UNITS_PER_MM)
    if not (1 <= units <= 65535):
        raise ValueError('Distance rounds to zero or exceeds 16-bit payload')
    return packet(op, units.to_bytes(2, 'little')), units


def parse_distance(value):
    """Allow comma or dot decimals, like `3 6,5` or `4 6.5`."""
    try:
        mm = float(value.strip().replace(',', '.'))
    except ValueError as exc:
        raise ValueError('Enter a distance in mm, e.g. 6 or 6.5') from exc
    # Include safety / rounding validation in the parser.
    motion_packet(0xA1, mm)
    return mm


def parse_menu_command(line):
    parts = line.strip().split()
    if not parts:
        return '', None
    if parts[0] in ('3', '4') and len(parts) == 2:
        return parts[0], parse_distance(parts[1])
    if len(parts) == 1 and parts[0] in ('0', '1', '2', '3', '4'):
        return parts[0], None
    raise ValueError('Expected 0, 1, 2, 3 [mm], or 4 [mm]')


class Decoder:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        while len(self.buffer) >= 8:
            if self.buffer[:2] != b'Qx':
                del self.buffer[0]
                continue
            size = int.from_bytes(self.buffer[4:6], 'little') + 8
            if size > 4096:
                del self.buffer[0]
                continue
            if len(self.buffer) < size:
                break
            frame = bytes(self.buffer[:size])
            if frame[-1] != 255 or crc8(frame[6:-2]) != frame[-2]:
                del self.buffer[0]
                continue
            del self.buffer[:size]
            frames.append(frame)
        return frames


def check_layout():
    if PRINT_WIDTH_PX <= 0 or PRINT_WIDTH_PX % 8:
        raise ValueError('PRINT_WIDTH_PX must be a positive multiple of 8')
    if not (2 <= LABEL_WIDTH_PX <= PRINT_WIDTH_PX):
        raise ValueError('LABEL_WIDTH_PX must be between 2 and PRINT_WIDTH_PX')
    if LABEL_LENGTH_PX < 2:
        raise ValueError('LABEL_LENGTH_PX must be at least 2')
    if not (0 <= X_OFFSET_PX <= PRINT_WIDTH_PX - LABEL_WIDTH_PX):
        raise ValueError('X_OFFSET_PX places the label outside the printhead')
    if not (1 <= LINE_WIDTH_PX <= LABEL_WIDTH_PX):
        raise ValueError('Invalid LINE_WIDTH_PX')


def make_cross_row(y):
    """One 384-dot A2 raster, packed LSB-first as in the successful test.

    The X reaches the four mathematical corners of LABEL_WIDTH_PX x
    LABEL_LENGTH_PX. Pixels are clipped to this label rectangle.
    """
    if not 0 <= y < LABEL_LENGTH_PX:
        raise ValueError('Row index outside label')
    row = bytearray(PRINT_WIDTH_PX // 8)
    # Intersections with the two label diagonals in label-local coordinates.
    left_to_right = round((LABEL_WIDTH_PX - 1) * y / (LABEL_LENGTH_PX - 1))
    right_to_left = LABEL_WIDTH_PX - 1 - left_to_right

    for center in (left_to_right, right_to_left):
        # At 2 px, put one pixel on either side of each diagonal location.
        for dx in range(-(LINE_WIDTH_PX // 2),
                        (LINE_WIDTH_PX + 1) // 2):
            x = center + dx
            if 0 <= x < LABEL_WIDTH_PX:
                absolute_x = X_OFFSET_PX + x
                row[absolute_x // 8] |= 1 << (absolute_x % 8)
    return bytes(row)


def print_packets():
    check_layout()
    # Exact working captured FunPrint control recipe, except raster content/length.
    commands = [packet(0xF2, b'\x01\xc8'), packet(0xA3, b'\0'),
                packet(0xA4, b'\x34'),
                packet(0xA6, bytes.fromhex('aa551738445f5f5f44382c')),
                packet(0xAF, bytes.fromhex('983a')), packet(0xBE, b'\0'),
                packet(0xBD, b'\x0a')]
    for y in range(LABEL_LENGTH_PX):
        commands.append(packet(0xA2, make_cross_row(y)))
    commands += [packet(0xBD, b'\x0a'),
                 packet(0xA6, bytes.fromhex('aa55170000000000000017')),
                 packet(0xA3, b'\0'), packet(0xA3, b'\0')]
    # Intentionally no fixed-distance A1 feed on label stock.
    return commands


async def choose_command():
    """Keep asyncio/BLE notifications running during console input."""
    print('\n' + '=' * 49)
    print('  1 - Отправить F0 (позиционирование этикетки)')
    print('  2 - Напечатать один крест X (40 x 30 мм)')
    print('  3 <мм> - Подать бумагу вперёд (A1), например: 3 6')
    print('  4 <мм> - Втянуть бумагу назад (A0), например: 4 6')
    print('  0 - Отключиться и завершить скрипт')
    print('=' * 49)
    try:
        return (await asyncio.to_thread(input, 'Выбери команду [0/1/2/3/4]: ')).strip()
    except EOFError:
        return '0'


async def experiment(args, log):
    from bleak import BleakClient
    decoder = Decoder()
    f0_reply = asyncio.Queue()
    ready = asyncio.Event()
    ready.set()
    faults = []

    def notify(sender, data):
        log('RX ' + bytes(data).hex(' ').upper())
        for frame in decoder.feed(data):
            op, payload = frame[2], frame[6:-2]
            if frame[3] != 1:
                continue
            if op == 0xF0:
                f0_reply.put_nowait(payload)
            elif op == 0xAE and payload == b'\x10':
                ready.clear()
            elif op == 0xAE and payload == b'\0':
                ready.set()
            elif op == 0xA3 and payload and payload[0] in (1, 9, 4, 8):
                faults.append(f'Printer error/status: {payload.hex(" ")}')

    def check():
        if faults:
            raise RuntimeError(faults[0])

    log('Connecting to ' + args.address)
    async with BleakClient(args.address, timeout=20) as client:
        await client.start_notify(NOTIFY, notify)
        await asyncio.sleep(1)

        async def send(frame):
            check()
            # Raster is large: omit per-row A2 TX hex dump entirely.
            # Print operation logs a compact single summary instead.
            if frame[2] != 0xA2:
                log('TX ' + frame.hex(' ').upper())
            for offset in range(0, len(frame), 20):
                await asyncio.wait_for(ready.wait(), 10)
                check()
                await asyncio.wait_for(
                    client.write_gatt_char(WRITE, frame[offset:offset+20], response=False), 10
                )
                await asyncio.sleep(0.01)

        async def position(label):
            log(label)
            while not f0_reply.empty():
                f0_reply.get_nowait()
            await send(F0)
            try:
                reply = await asyncio.wait_for(f0_reply.get(), 15)
            except asyncio.TimeoutError:
                raise RuntimeError('No F0 reply within 15 seconds. Stopping; no retry.') from None
            if reply != b'\x01':
                raise RuntimeError('Unexpected F0 reply: ' + reply.hex(' '))
            check()
            log('Captured F0 reply received (01); check physical paper position.')
            await asyncio.sleep(0.5)

        log('Connected. Menu is ready; no automatic positioning or printing.')
        f0_count = 0
        print_count = 0
        feed_count = 0
        retract_count = 0
        while True:
            check()
            raw_command = await choose_command()
            try:
                command, distance_mm = parse_menu_command(raw_command)
                if command in ('3', '4') and distance_mm is None:
                    # Allow both `3 6` and `3` followed by a separate prompt.
                    distance_text = await asyncio.to_thread(
                        input, 'Расстояние, мм (например 6 или 6,5): '
                    )
                    distance_mm = parse_distance(distance_text)
            except (ValueError, EOFError) as error:
                print(f'Неверная команда: {error}')
                continue

            if command == '0':
                log('User requested disconnect. Closing BLE connection.')
                break
            if command == '1':
                f0_count += 1
                await position(f'F0 #{f0_count}: Positioning on user request')
                log('F0 finished. Inspect physical position; choose the next command.')
            elif command == '2':
                print_count += 1
                log(f'PRINT #{print_count}: Sending ONE X, '
                    f'{LABEL_WIDTH_PX} x {LABEL_LENGTH_PX} dots, '
                    f'offset {X_OFFSET_PX} in {PRINT_WIDTH_PX}-dot raster.')
                log(f'TX A2: {LABEL_LENGTH_PX} raster rows '
                    f'({PRINT_WIDTH_PX // 8} data bytes each); hex omitted from log.')
                for frame in print_packets():
                    await send(frame)
                log(f'Settling for {args.settle:g} seconds; this is NOT a print-completion ACK.')
                await asyncio.sleep(args.settle)
                check()
                log('Raster sent. Inspect the label; choose the next command.')
            elif command in ('3', '4'):
                # No automatic positioning before/after motion.
                op = 0xA1 if command == '3' else 0xA0
                frame, units = motion_packet(op, distance_mm)
                if op == 0xA1:
                    feed_count += 1
                    number = feed_count
                    direction = 'FEED (forward)'
                else:
                    retract_count += 1
                    number = retract_count
                    direction = 'RETRACT (backward)'
                scale_note = ('A1 scale estimated from measurements' if op == 0xA1
                              else 'A0 scale assumed equal to A1; unverified')
                log(f'{direction} #{number}: requested {distance_mm:g} mm; '
                    f'payload {units} units (4 units/mm). '
                    f'{scale_note}; probable ~25 mm maximum per command.')
                await send(frame)
                log('Motion command sent (not a completion ACK). '
                    'Measure actual movement before choosing the next command.')
            else:
                print('Неизвестная команда. Введи 0, 1, 2, 3 <мм> или 4 <мм>.')
        log(f'Session finished: {f0_count} F0, {print_count} print, '
            f'{feed_count} A1 feed, {retract_count} A0 retract operation(s).')


def is_black(row, x):
    return bool(row[x // 8] & (1 << (x % 8)))


def self_test():
    assert packet(0xF0, bytes.fromhex('008320')) == F0
    assert motion_packet(0xA1, 6)[0] == packet(0xA1, bytes.fromhex('1800'))
    assert motion_packet(0xA0, 6)[0] == packet(0xA0, bytes.fromhex('1800'))
    assert motion_packet(0xA1, 6)[1] == 24
    assert motion_packet(0xA1, 1)[1] == 4
    assert motion_packet(0xA0, 1)[1] == 4
    assert motion_packet(0xA1, 25)[1] == 100
    assert motion_packet(0xA0, 25)[1] == 100
    assert parse_menu_command('3 6,5') == ('3', 6.5)
    assert parse_menu_command('4') == ('4', None)
    assert parse_menu_command('3 25') == ('3', 25.0)
    for invalid in (0, -1, 0.01, float('nan'), float('inf'), MAX_MOTION_MM + 0.1):
        try:
            motion_packet(0xA1, invalid)
        except ValueError:
            pass
        else:
            raise AssertionError('Unsafe motion distance was accepted')
    check_layout()
    frames = print_packets()
    raster_frames = [frame for frame in frames if frame[2] == 0xA2]
    assert len(raster_frames) == LABEL_LENGTH_PX
    assert all(len(frame[6:-2]) == PRINT_WIDTH_PX // 8 for frame in raster_frames)
    assert not any(frame[2] == 0xA1 for frame in frames)
    # Both diagonals must end at all four calculated label corners.
    start, end = make_cross_row(0), make_cross_row(LABEL_LENGTH_PX - 1)
    for row in (start, end):
        assert is_black(row, X_OFFSET_PX)
        assert is_black(row, X_OFFSET_PX + LABEL_WIDTH_PX - 1)
    # No mark can escape the label width, regardless of row.
    for y in range(LABEL_LENGTH_PX):
        row = make_cross_row(y)
        assert all(not is_black(row, x) for x in range(X_OFFSET_PX))
        assert all(not is_black(row, x) for x in range(X_OFFSET_PX + LABEL_WIDTH_PX, PRINT_WIDTH_PX))
    # Check that the central crossing is present in the mid-height rows.
    mid = make_cross_row(LABEL_LENGTH_PX // 2)
    assert any(is_black(mid, x) for x in range(X_OFFSET_PX + LABEL_WIDTH_PX // 2 - 4,
                                                X_OFFSET_PX + LABEL_WIDTH_PX // 2 + 4))
    decoder = Decoder()
    recovered = []
    stream = b''.join(frames)
    for start_idx in range(0, len(stream), 13):
        recovered += decoder.feed(stream[start_idx:start_idx+13])
    assert recovered == frames and not decoder.buffer
    ack = bytes.fromhex('5178f00101000107ff')
    assert Decoder().feed(ack) == [ack]
    assert Decoder().feed(ack[:-2] + b'\x00\xff') == []
    print(f'PASS: captured F0, CRC, {LABEL_LENGTH_PX} rows, LSB-first X in '
          f'{LABEL_WIDTH_PX} x {LABEL_LENGTH_PX}, all 4 corners, no automatic A1; '
          'A1=24 units at 6 mm (measured scale); A0 assumed same; max 25 mm.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--address', default='EB:11:06:26:77:26')
    parser.add_argument('--settle', type=float, default=3,
                        help='2..15 seconds after sending the raster; not a completion ACK')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        self_test()
        return 0
    if not 2 <= args.settle <= 15:
        parser.error('--settle must be between 2 and 15')
    check_layout()
    path = Path.cwd() / ('thermoprinter-label-test-' + datetime.now().strftime('%Y%m%d-%H%M%S-%f') + '.log')
    started = time.monotonic()
    with path.open('x', encoding='utf-8') as output:
        def log(message):
            line = f'{time.monotonic()-started:8.3f}s {message}'
            print(line, flush=True)
            output.write(line + '\n')
            output.flush()
        log('Log: ' + str(path))
        log('Experimental captured V5G recipe; interactive commands 1=F0 2=X 3=A1(mm) 4=A0(mm) 0=exit; no retries; A2 TX hex omitted.')
        try:
            asyncio.run(experiment(args, log))
            return 0
        except KeyboardInterrupt:
            log('Interrupted. Disconnecting is not a motor-stop command. Switch printer off if needed.')
            return 130
        except Exception as error:
            log(f'ERROR: {type(error).__name__}: {error}')
            log('No retry. Some data may already have reached the printer; inspect before another run.')
            return 1


if __name__ == '__main__':
    raise SystemExit(main())
