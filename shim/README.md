# pyproject-runner-shim

pyproject-runner-shim provides a tiny convenience script to shorten the command used to execute
pyproject-runner tasks from `uv run rr TASK ...` to `rr TASK ...`. This assumes the shim is being
executed from the directory or subdirectory of a uv-managed project with pyproject-runner installed
in the project's virtual environment.

[pyproject-runner](../README.md) provides a simple, project-oriented method of defining developer scripts.
It is a simple task runner, similar to [taskipy](https://pypi.org/project/taskipy/) or [Poe the Poet](https://pypi.org/project/poethepoet/), for running tasks
defined in a *pyproject.toml* file.


## Installation

Install with `uv tool`:

```console
$ uv tool install pyproject-runner-shim
```

If uv complains that the tool bin directory is not on the *PATH* environment variable, use
`uv tool update-shell` to update the *PATH*, or manually add it to your shell's configuration file.
See the [uv tool documentation](https://docs.astral.sh/uv/concepts/tools/#tool-executables) for more information.


## How it works

The package consists of a single `rr` script, mirroring the name of the main script from the
pyproject-runner package, which uv installs into the uv tool bin directory. Using only the Python
standard library, the script simply executes `uv run -- python3 -m pyproject_runner`, passing along
any arguments provided to the script. This allows the shim to be used with any number of uv-based
projects that may require different versions of pyproject-runner. uv automatically determines which
virtual environment to used based on the current working directory.


## Changelog

View the full changelog [here](https://github.com/avantus-tech/pyproject-runner/releases).


## License

pyproject-runner is licensed under a [3-Clause BSD licence](LICENSE.txt).
