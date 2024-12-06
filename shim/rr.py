"""Shim for running pyproject-runner directly, without using `uv run`."""

import os
import sys


def main():
    """Entry point for rr shim."""
    try:
        os.execvp("uv", ["uv", "run", "--", "python3", "-m", "pyproject_runner", *sys.argv[1:]])  # noqa: S606, S607
    except OSError as exc:
        print(f"error: {exc}: uv", file=sys.stderr)  # noqa: T201
    sys.exit(1)


if __name__ == "__main__":
    main()
