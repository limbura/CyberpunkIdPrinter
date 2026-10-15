"""Run only when the printer is loaded with continuous paper.

CYBERWALL_TIMINI_PATH must point to the pinned external checkout.
"""
import argparse
import asyncio
from cyberwall_id_printer import MediaConfig, Printer


async def main(address):
    async with Printer(media=MediaConfig(width_mm=40, height_mm=30, roll_width_mm=57)) as printer:
        print(await printer.connect(address))
        print(await printer.print_id("123456789", "Роза фон Праунхайм"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("address")
    asyncio.run(main(parser.parse_args().address))
