"""
Діагностичний скрипт: відкриває порт і друкує сирі рядки, що надходять.
Без парсингу float, без GUI — лише перевірка, що дані взагалі доходять.

Приклад:
    python read_test.py --port COM8 --baud 115200
"""

import argparse

import serial

from simulator import BAUD_RATES


def main() -> None:
    parser = argparse.ArgumentParser(description="Читання сирих рядків з COM-порту")
    parser.add_argument("--port", required=True)
    parser.add_argument("--baud", type=int, default=115200, choices=BAUD_RATES)
    args = parser.parse_args()

    with serial.Serial(args.port, args.baud, timeout=1) as ser:
        print(f"Слухаю {args.port} @ {args.baud}. Ctrl+C для зупинки.")
        try:
            while True:
                raw = ser.readline()
                if raw:
                    print(raw.decode("ascii", errors="replace").strip())
        except KeyboardInterrupt:
            print("\nЗупинено.")


if __name__ == "__main__":
    main()
