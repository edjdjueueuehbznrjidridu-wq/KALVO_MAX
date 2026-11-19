#!/usr/bin/env python3
"""Run KALVO's repeatable local validation suite."""
from __future__ import annotations

import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]


def run(command: list[str]) -> None:
    print("\n$", " ".join(command), flush=True)
    completed = subprocess.run(command, cwd=ROOT, check=False)
    if completed.returncode:
        raise SystemExit(completed.returncode)


def main() -> None:
    run([sys.executable, "-m", "pytest", "-q"])
    run([sys.executable, "-m", "compileall", "-q", "main.py", "apex", "tests", "tools"])
    print("\nKALVO validation completed successfully.")


if __name__ == "__main__":
    main()
