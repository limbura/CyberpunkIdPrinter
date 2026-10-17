"""Interactive hardware test using cyberwall_id_printer AND external TiMini.

Run from the repository: python examples/labels_v2.py --timini-path ../TiMini-Print
No job is resumed/replayed automatically, even after reconnecting.
"""
import argparse
import asyncio
from datetime import datetime
import json
import logging
from PIL import Image
from cyberwall_id_printer import (
    LabelPrinter, LabelSettings, RemovalRequired, LabelOperationError,
    MediaConfig, PaperType, LabelMode,
)


def arguments():
    p = argparse.ArgumentParser(description='Термопринтер: проверка стикеров, вариант 2')
    p.add_argument('--address', default='EB:11:06:26:77:26')
    p.add_argument('--timini-path', default=None)
    p.add_argument('--feed-mm', type=float, default=15)
    p.add_argument('--retract-mm', type=float, default=25)
    p.add_argument('--confirmation', choices=['always', 'recovery', 'never'], default='always')
    p.add_argument('--attempts', type=int, default=2)
    p.add_argument('--timeout', type=float, default=20)
    p.add_argument('--scan-timeout', type=float, default=15)
    p.add_argument('--position-timeout', type=float, default=20)
    p.add_argument('--settle', type=float, default=3)
    return p.parse_args()


def mark():
    # Same 320 x 240 X, left aligned, as the successful variant-2 experiment.
    image = Image.new('1', (384, 240), 1)
    for y in range(240):
        x = round(319 * y / 239)
        for centre in (x, 319 - x):
            for dx in (-1, 0):
                if 0 <= centre + dx < 320:
                    image.putpixel((centre + dx, y), 0)
    return image


async def main(args):
    log = f'thermoprinter-labels-v2-{datetime.now():%Y%m%d-%H%M%S}.log'
    logging.basicConfig(filename=log, level=logging.DEBUG, encoding='utf-8',
                        format='%(asctime)s %(name)s %(levelname)s %(message)s')
    print('Журнал:', log)
    print('Экспериментальный режим. Ожидания движения — по времени, не подтверждение завершения печати.')
    if args.confirmation == 'never':
        print('Подтверждения отключены: перед следующей печатью самостоятельно снимайте наклейку.')

    def event(e):
        logging.info('STATE %s', json.dumps(e, ensure_ascii=False))
        print('  ', e)

    printer = LabelPrinter(
        timini_path=args.timini_path,
        media=MediaConfig(paper_type=PaperType.LABELS, roll_width_mm=40, offset_x_mm=-4),
        settings=LabelSettings(feed_mm=args.feed_mm, retract_mm=args.retract_mm,
                               confirmation=args.confirmation, position_timeout=args.position_timeout,
                               settle_seconds=args.settle),
        timeout=args.timeout, connect_attempts=args.attempts,
        scan_timeout=args.scan_timeout, on_event=event)

    async def monitor():
        previous_error = None
        while True:
            await asyncio.sleep(1)
            if printer.state.value == 'disconnected':
                try:
                    print('Повторное подключение…')
                    await printer.connect(args.address)
                except Exception:
                    logging.exception('Reconnect failed; no job replay')
                    await asyncio.sleep(5)
                continue
            try:
                await printer.poll()
                previous_error = None
            except Exception as exc:
                text = str(exc)
                if text != previous_error:
                    print('\nСостояние:', text, '\nПосле устранения причины выберите восстановление или переподключение.')
                    logging.exception('Status poll failed')
                    previous_error = text
                if not await printer.check_connection():
                    try:
                        await printer.disconnect()
                    except Exception:
                        logging.exception('Disconnect cleanup failed')
                    await asyncio.sleep(5)

    task = None
    try:
        try:
            await printer.connect(args.address)
        except Exception as exc:
            print('Подключение не удалось:', exc, '\nМожно повторить пунктом 5.')
            logging.exception('Connect failed; no paper motion requested')
        task = asyncio.create_task(monitor())
        while True:
            try:
                choice = (await asyncio.to_thread(input,
                '\n1 — печать креста 40×30 мм; 2 — печать ID без имени\n'
                '3 — все выступающие наклейки сняты, крышка закрыта\n'
                '4 — восстановить положение (только вперёд); 5 — переподключиться\n'
                '0 — выйти\n> ')).strip()
            except EOFError:
                break
            try:
                if choice == '0':
                    break
                if choice == '1':
                    print(await printer.print_image(mark()))
                elif choice == '2':
                    print(await printer.print_id('123456789', mode=LabelMode.ID_ONLY))
                elif choice == '3':
                    await printer.confirm_removed()
                    print('Подтверждено. Для печати выберите 1 или 2.')
                elif choice == '4':
                    await printer.recover()
                elif choice == '5':
                    await printer.disconnect()
                    await printer.connect(args.address)
            except RemovalRequired:
                print('Снимите ВСЕ выступающие наклейки, закройте крышку, выберите 3, затем снова 1 или 2.')
            except Exception as exc:
                print('Ошибка:', exc)
                if isinstance(exc, LabelOperationError):
                    print('Этап:', exc.stage, '; печать могла произойти:', exc.may_have_printed)
                    print('Причина:', exc.__cause__)
                logging.exception('Operation failed; no automatic retry')
    finally:
        if task:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        await printer.disconnect()


if __name__ == '__main__':
    try:
        asyncio.run(main(arguments()))
    except KeyboardInterrupt:
        print('\nОстановлено. Перед следующей печатью положение нужно восстановить.')
