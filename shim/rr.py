"""Shim for running pyproject-runner directly, without using `uv run`.

This script is designed to be used with uv. It can be installed in the project
virtual environment, by including it in the *dev* dependency group in
*pyproject.toml*, or as a tool, using `uv tool install pyproject-runner-shim`.
Installing it as a tool allows it to be used with multiple projects, with or
without installing pyproject-runner in the project's virtual environment. If
pyproject-runner is installed in the project's virtual environment, then it
will be loaded and run using the project's python, thus allowing different
versions of pyproject-runner to be used with different projects and updated
when syncing the project. Otherwise, it will use `uv tool` to run
pyproject-runner from en ephemeral virtual environment.
"""

import os
import subprocess
import sys

RUN_KEY = "__PYPROJECT_RUNNER_SHIM_RUN__"


def main():
    """Entry point for rr shim."""
    if len(sys.argv) > 1 and sys.argv[1] == RUN_KEY:
        del sys.argv[1]
        try:
            from pyproject_runner.__main__ import main
        except ModuleNotFoundError:
            run(["uv", "tool", "run", "--from", "pyproject-runner", "rr", *sys.argv[1:]])
        else:
            main(prog_name="rr")
    else:
        run(["uv", "run", "--frozen", "--", "python", __file__, RUN_KEY, *sys.argv[1:]])


def run(cmd: list[str]) -> None:
    """Run the given command."""
    try:
        if sys.platform == "win32":
            sys.exit(subprocess.run(cmd).returncode)  # noqa: S603
        else:
            os.execvp(cmd[0], cmd)  # noqa: S606
    except KeyboardInterrupt:
        pass
    except OSError as exc:
        print(f"error: {exc}: {cmd[0]}", file=sys.stderr)  # noqa: T201
    sys.exit(1)


if __name__ == "__main__":
    main()
