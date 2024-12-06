# pyproject-runner

pyproject-runner provides a simple, project-oriented method of defining developer scripts. It is
a simple task runner, similar to [taskipy](https://pypi.org/project/taskipy/) or [Poe the Poet](https://pypi.org/project/poethepoet/), for running tasks defined
in a *pyproject.toml* file.

While pyproject-runner is best used with [uv](https://docs.astral.sh/uv/), it does not require it, and can benefit any
project wishing to define common developer tasks.

Inspired by [Rye's run command](https://rye.astral.sh/guide/commands/run/), pyproject-runner will feel familiar to those who have used
`rye run`. Switching from Rye to uv with pyproject-runner requires only a few changes to the
*pyproject.toml* file. See [differences from `rye run`](#differences-from-rye-run) below for more
information.


## Installation

Install pyproject-runner with uv or pip:

```console
# Install in a uv-managed virtualenv:
$ uv pip install pyproject-runner

# Or install using pip:
$ pip install pyproject-runner
```

### Using with uv-managed projects

Add pyproject-runner to the *dev* group of the project's *pyproject.toml* file, and it will be
automatically installed when uv syncs the virtual environment:

```console
$ uv add --dev pyproject-runner
```

Or add it manually:

```toml
[dependency-groups]
dev = [
    "pyproject-runner",
]
```

### Convenience shim

It is also recommended to install [pyproject-runner-shim](shim/README.md), which provides a shortcut for
running tasks. uv doesn't recommend activating virtual environments, but suggests using `uv run`
to execute scripts in the virtual environment. The shim allows shortening
`uv run rr TASK ...` to `rr TASK ...` saving valuable keystrokes.

### Requirements

pyproject-runner requires Python 3.10 or higher because it makes use of [structural pattern matching](https://docs.python.org/3/reference/compound_stmts.html#the-match-statement)
when parsing the *pyproject.toml* file.


## Usage

Define tasks in the *pyproject.toml* file:

```toml
[tool.pyproject-runner.tasks]
devserver = "flask run --app ./hello.py --debug"
http = { cmd = ["python", "-mhttp.server", "8000"] , help = "Start a web server for the project." }
check = { pre = ["mypy", "lint"], cmd = "pytest", post = ["uv build"] }
lint = { pre = ["lint:ruff", "lint:flake8"] }
"lint:flake8" = "uvx flake8 src"
"lint:ruff" = "uvx ruff check src"
ci = "uv run scripts/ci-build.py"  # run script in uv-managed venv using inline script metadata
```

Then execute the tasks using the `rr` command:

```console
$ rr devserver 

# Pass additional arguments to the task
$ rr lint:ruff --show-fixes --statistics
```

**Note:** In a uv-managed project without pyproject-runner-shim installed, it is necessary to
prefix the `rr` command with `uv run` if the virtual environment is not activated.


## Configuration

pyproject-runner is configured using the `tool.pyproject-runner` table in the *pyproject.toml* file.

### `tool.pyproject-runner.tasks`

This key is used to register custom tasks that are exposed via the `rr` task runner. The value for
this key is a *table* (dictionary) of tasks where each key is the name of a task, and the value is
the task definition. Tasks can be defined using a *string*, an *array*, or a *table*.

```toml
[tool.pyproject-runner.tasks]
# These three options are equivalent:
devserver = "flask run --app ./hello.py --debug"
devserver-alt = ["flask", "run", "--app", "./hello.py", "--debug"]
devserver-explicit = { cmd = "flask run --app ./hello.py --debug" }
```

Using a table allows for additional configuration using the keys below. Tasks must define at least
one of `cmd`, `pre`, or `post`.

#### `cmd`

The command to execute. This is either a `string` or an `array` of arguments. It is executed
directly without a shell, so shell-specific semantics are not directly supported.

```toml
[tool.pyproject-runner.tasks]
devserver = { cmd = "flask run --app ./hello.py --debug" }
http = { cmd = ["python", "-mhttp.server", "8000"] }
```

#### `pre` and `post`

These keys can be used with or instead of `cmd` to invoke one or more tasks along with or instead
of `cmd`. The value of each is an *array* of tasks, with optional arguments. Each task must be a
string or an *array* of strings, similar to `cmd`, but the command is limited to tasks defined in
the *pyproject.toml* file and to scripts installed in the virtual environment. Tasks will be
executed sequentially until all are complete or a task fails. All other keys below, except `help`,
are only used with `cmd`, if it is given. `pre` lists tasks that will run before `cmd`, and `post`
lists tasks that will run afterward.

```toml
[tool.pyproject-runner.tasks]
check = { pre = ["mypy", "lint"], cmd = "pytest", post = ["uv build"] }
lint = { pre = ["lint:ruff", "lint:flake8"] }
"lint:flake8" = "uvx flake8 src"
"lint:ruff" = "uvx ruff check src"
```

#### `cwd`

Commands execute in the current directory by default. Set `cwd` to a *string* to change the working
directory before executing the command. The initial working directory is saved in the *INITIAL_DIR*
environment variable.  See [Paths](#paths) and [Execution environment](#execution-environment) below.

```toml
[tool.pyproject-runner.tasks]
# Ensure tool can execute from subdirectories in the project
tool = { cmd = "uv run tools/tool.py", cwd = "!" }
```

#### `env`

This key is used to set environment variables before executing a task. It can be a *table* or a
*string*. If a *string* is provided, the value is processed as if read from a file, like
[`env-file`](#env-file) below.

```toml
[tool.pyproject-runner.tasks]
devserver = { cmd = "flask run --debug", env = { FLASK_APP = "./hello.py" } }
http = { cmd = ["python", "-mhttp.server", "8000"], env = """
# Use the user's web root
WEB_ROOT=$HOME/Public
""" }
```

#### `env-file`

This is similar to `env` above, but it reads environment variables from a file rather than setting
them directly. See [Environment file syntax](#environment-file-syntax) and [Paths](#paths) below.

```toml
[tool.pyproject-runner.tasks]
# Set the environment from a file in the project root
devserver = { cmd = "flask run --debug", env-file = "!/.dev.env" }
```

#### `help`

A *string* with a help message, describing what the task does, that will be printed with the task
name when the `-l/--list` option is used. 

```toml
[tool.pyproject-runner.tasks]
devserver = { cmd = "flask run --app ./hello.py --debug", help = "Start a development server in debug mode" }
```

### `tool.uv.managed`

Optional setting for projects that are not using uv.

Set to `false` for projects not using uv or when uv is not managing the virtual environment,
indicating that the `VIRTUAL_ENV` environment variable will be used to find an alternate path
for the virtual environment. It is unnecessary to set this if the default `.venv` is used.

See the [uv documentation](https://docs.astral.sh/uv/reference/settings/#managed) for more information on this setting.

### Environment file syntax

Environment variables may be loaded from a file or string, expanding variables as needed. The
syntax is similar to bash syntax, but simplified and relaxed.

Each variable assignment must start on a new line and include a variable name, followed by an equal
(=), and then an optional value. White space before and after the name and equal and at the end of
the line are ignored. Values may be optionally quoted to preserve leading and trailing white space.
Variables may be unset by excluding the value.

Values may include other variables using bash-like variable substitution: \$name or \${name}.
Unless escaped, variable expansion will occur in unquoted, double-quoted ("), and triple
double-quoted values (\"""). Any character, including quotes and newlines, may be escaped using a
backslash (\\).

Like bash, variable substitution in single-quoted (') and triple single-quoted (''') values are not
expanded, and backslash escapes are ignored.

Line comments begin at an unquoted and unescaped hash/pound (#) at the beginning of a line or after
white space, and continue to the end of the line.

#### Example environment file

```shell
# The following line unsets var
var =

bar = "Backslashes escape special characters: \\, \", and \$"
foo = 'Backslash \ escapes and $bar substitution are ignored in single quotes'
PATH=$PROJECT_DIR/scripts:${PATH}  # quotes and curly-braces are optional

other_var = """quotes preserve
newlines and tabs """
```

Expanding the file above results in something like the following Python code:
```
del var
bar = 'Backslashes escape special characters: \\, ", and $'
foo = 'Backslash \\ escapes and $bar substitution are ignored in single quotes'
PATH = '/home/user/pyproject-runner/scripts:/home/user/pyproject-runner/.venv/bin:/home/user/.cargo/bin:/usr/local/bin:/usr/bin'
other_var = 'quotes preserve\nnewlines and tabs '
```

### Paths

Paths beginning with a bang atom (!) are considered relative to the project root, that is the
directory containing the *pyproject.toml* file. Otherwise, paths are treated normally: absolute
paths are absolute, and relative paths are considered relative to the current working directory,
unless stated otherwise. This applies to commands (but not command arguments), to `cwd`, and
`env-file`.

So, if the *pyproject.toml* file is in `/user/project`, and the current working directory is
`/user/project/src/package`, then paths are translated as follows:

| Given Path     | Effective Path                |
|----------------|-------------------------------|
| !              | /user/project                 |
| !/             | /user/project                 |
| !/..           | /user                         |
| !/scripts/lint | /user/project/scripts/lint    |
| foo            | /user/project/src/package/foo |
| ./foo          | /user/project/src/package/foo |
| ..             | /user/project/src             |
| ../bar         | /user/project/src/bar         |
| /usr/bin/mypy  | /usr/bin/mypy                 |

### Environment variables

Setting the following environment variables changes pyproject-runner's behavior.

FORCE_COLOR
: Force color output regardless of terminal support.

NO_COLOR
: Disable color output (takes precedence over `FORCE_COLOR`).

UV_PROJECT_ENVIRONMENT
: Specifies the path to the directory to use for a project virtual environment (see
  [uv's documentation](https://docs.astral.sh/uv/configuration/environment/#uv_project_environment) for more info).

VIRTUAL_ENV
: Specifies the path to the project's virtual environment. Ignored unless `tool.uv.managed` is set to `false`.

## Execution environment

Several environment variables are set before executing tasks or processing `env-file` files. Paths
are absolute unless otherwise specified.

VIRTUAL_ENV
: Root of the project's virtual environment.

VIRTUAL_ENV_BIN
: Directory in the project's virtual environment containing the python executable and scripts.

INITIAL_DIR
: Current working directory at the time pyproject-runner was executed.

PROJECT_DIR
: Directory where the *pyproject.toml* file was found.

WORKSPACE_DIR
: Workspace root, if the project is part of a workspace; otherwise it is unset.

PATH
: Set or modified so that `$VIRTUAL_ENV_BIN` is the first path.

`PYTHONHOME` is removed from the environment, if it is set.


## Differences from `rye run`

While pyproject-runner started as a feature-parity re-implementation of `rye run` (hence the `rr`
script name), it was also intended as a project to experiment with new features and fixing problems
with `rye run`. It was never intended that it would maintain feature-parity. This is especially
true now that development of Rye has stopped. Here are some of the key differences for those coming
to pyproject-runner from Rye.

### Call task type is unsupported

The `call` task type, supported by `rye run`, is *not* supported by pyproject-runner because it is merely
shorthand for calling python, and is easily reproduced:

```toml
[tool.pyproject-runner.tasks]
# serve = { call = "http.server" }
server = { cmd = ["python", "-c", "import http; http.server()"] }
# help = { call = "builtins:help" }
help = { cmd = "python -c help()" }
# hello-world = { call = "builtins:print('Hello World!')" }
hello-world = ["python", "-c", "print('Hello World!')"]
```

### Task chains

With Rye, task chains use the `chain` command type, which suffer from the limitation that none of
the tasks in the chain can be passed options or arguments. In pyproject-runner, chains are
supported by providing `pre` and/or `post` tasks. This is a bit more powerful because they can be
provided along with `cmd`, where the command can consume arguments. Or use them without `cmd` to
mimic Rye's chains.

```toml
[tool.pyproject-runner.tasks]
# lint = { chain = ["lint:ruff", "lint:flake8"] }  # Rye chain
lint = { pre = ["lint:ruff", "lint:flake8"] }  # pyproject-runner chain
"lint:ruff" = "uvx ruff check src"
"lint:flake8" = "uvx flake8 src"
```

### Relative `env-file` paths

Rye looks for relative `env-file` paths relative to the project root, while pyproject-runner
searches for them relative to the current directory, unless prefixed with '!/'. See [Paths](#paths)
for more information.

### Tasks can mask scripts

Tasks in pyproject-runner can have the same name as an installed script (i.e., to provide default
arguments). Scripts take precedence over Rye tasks, making it impossible to create tasks with the
same name.


## Future features

Below is a list of features that might be implemented in the future (no guarantees on any of them).

 - [ ] Task groups (group tasks under a common parent command, like git or uv)
 - [ ] Markers for platform-specific commands, similar to Python requirements (e.g., `sys.platform == 'win32'`)
 - [ ] Run tasks defined in parent workspace from child project (allow defining tasks common to the whole workspace)
 - [ ] Task aliases? Short name matching?
 - [x] Add option to show task help
 - [ ] Shell completion
 - [ ] Define common environment variables in [tool.pyproject-runner.environment]?
 - [ ] Environment variable expansion in task definitions
 - [ ] Add ability to create shims for tasks and/or scripts

Do you have additional feature requests? Submit an issue or pull request.


## Frequently asked questions

* **Why not just use taskipy or Poe the Poet?**
  + They are both good projects, but neither were quite the right fit for my non-Poetry projects:
    - Both have many dependencies that are restricted to a narrow range of versions, which conflict
      with projects I work on that require newer versions of those packages.
    - Both were designed around Poetry.
    - Neither offer a shim to simplify use under uv.
  + pyproject-runner was created to solve those issues, and offers the following benefits:
    - It has only one dependency for Python >= 3.11, or two for Python 3.10, pinned only to the
      lowest compatible version.
    - It makes it easy to move from Rye to uv, or to use with new uv projects.
    - Offers a shim to reduce typing.
    - It's simple, fast, and small, with less than 1000 lines of code.


## License

pyproject-runner is licensed under a [3-Clause BSD licence](LICENSE.txt).
