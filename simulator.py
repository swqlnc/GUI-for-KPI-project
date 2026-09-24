"""
Симулятор пристрою для тестування GUI без реального обладнання.
"""

import argparse
import math
import time

import serial

BAUD_RATES = [9600, 57600, 115200]


def generate_sample(t: float) -> tuple[float, float, float, float, float, float]:
    """Повертає 6 синтетичних значень (канал 1: x1,y1,z1; канал 2: x2,y2,z2)."""
    x1 = 0.02 + 0.015 * math.sin(t * 0.9)
    y1 = -1.00 + 0.015 * math.sin(t * 0.7 + 1.1)
    z1 = 0.15 + 0.02 * math.sin(t * 1.3 + 2.0)
    x2 = 0.0012 * math.sin(t * 2.1)
    y2 = 0.0010 * math.sin(t * 1.7 + 0.6)
    z2 = 0.0021 * math.sin(t * 2.6 + 1.4)
    return x1, y1, z1, x2, y2, z2


def main() -> None:
    parser = argparse.ArgumentParser(description="Симулятор потоку даних для COM-порту")
    parser.add_argument("--port", required=True, help="Наприклад COM5 або /dev/pts/3")
    parser.add_argument("--baud", type=int, default=115200, choices=BAUD_RATES)
    parser.add_argument("--interval", type=float, default=0.1, help="Пауза між рядками, с")
    args = parser.parse_args()

    with serial.Serial(args.port, args.baud, timeout=1) as ser:
        print(f"Симулятор запущено на {args.port} @ {args.baud}. Ctrl+C для зупинки.")
        t = 0.0
        try:
            while True:
                t += args.interval
                values = generate_sample(t)
                line = "%.5f %.5f %.5f %.5f %.5f %.5f \r\n" % values
                ser.write(line.encode("ascii"))
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print("\nЗупинено.")


if __name__ == "__main__":
    main()