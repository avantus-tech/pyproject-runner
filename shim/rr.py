"""Shim for running pyproject-runner directly, without using `uv run`."""

import os
import subprocess
import sys


def main():
    """Entry point for rr shim."""
    cmd = ["uv", "run", "--", "python", "-m", "pyproject_runner", *sys.argv[1:]]
    try:
        if sys.platform == "win32":
            sys.exit(subprocess.run(cmd).returncode)  # noqa: S603
        else:
            os.execvp("uv", cmd)  # noqa: S606, S607
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        print(f"error: {exc}: uv", file=sys.stderr)  # noqa: T201
    sys.exit(1)


if __name__ == "__main__":
    main()
