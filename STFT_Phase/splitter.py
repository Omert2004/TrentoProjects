#!/usr/bin/env python3

import sys
import random
from pathlib import Path

WINDOW_SIZE = 15


def main():
    if len(sys.argv) != 4:
        print(
            f"Usage: {sys.argv[0]} <input_file> <num_files> <output_dir>"
        )
        sys.exit(1)

    input_file = Path(sys.argv[1])
    num_files = int(sys.argv[2])
    output_dir = Path(sys.argv[3])

    with open(input_file, "r") as f:
        lines = [line for line in f if line.strip()]

    if not lines:
        print("Input file is empty.")
        sys.exit(1)

    # Skip incomplete first line
    first_count = len(lines[0].split())
    if first_count < 256:
        print(f"Ignoring incomplete first line ({first_count} values).")
        lines = lines[1:]

    if not lines:
        print("No valid lines remain.")
        sys.exit(1)

    # Skip incomplete last line
    last_count = len(lines[-1].split())
    if last_count < 256:
        print(f"Ignoring incomplete last line ({last_count} values).")
        lines = lines[:-1]

    if not lines:
        print("No valid lines remain.")
        sys.exit(1)

    # Verify all remaining lines
    for i, line in enumerate(lines, start=1):
        count = len(line.split())

        if count != 256:
            print(
                f"Error: usable line {i} contains "
                f"{count} values instead of 256."
            )
            sys.exit(1)

    if len(lines) < WINDOW_SIZE:
        print(
            f"Error: only {len(lines)} complete lines available; "
            f"need at least {WINDOW_SIZE}."
        )
        sys.exit(1)

    output_dir.mkdir(parents=True, exist_ok=True)

    base_name = output_dir.name
    max_start = len(lines) - WINDOW_SIZE

    for i in range(1, num_files + 1):
        start = random.randint(0, max_start)

        window = lines[start:start + WINDOW_SIZE]

        output_path = output_dir / f"{base_name}{i}"

        with open(output_path, "w") as f:
            f.writelines(window)

    print(f"Generated {num_files} files in {output_dir}")


if __name__ == "__main__":
    main()