# pyproject-runner

pyproject-runner provides a simple, project-oriented method of defining developer scripts. It is
a simple task runner, similar to [taskipy](https://pypi.org/project/taskipy/) or [Poe the Poet](https://pypi.org/project/poethepoet/), for running tasks defined
in a *pyproject.toml* file.

Inspired by the [Rye's run command](https://rye.astral.sh/guide/commands/run/), pyproject-runner can be used as an almost-drop-in replacement
for `rye run`. While pyproject-runner is designed to be used with [uv](https://docs.astral.sh/uv/), it may also be used with
projects not utilizing uv. It was initially created to experiment with new features for `rye run`,
but it is now intended as a replacement for it as development on Rye has ceased. uv is taking Rye's
place and currently lacks a task runner feature.


## Installation

Install pyproject-runner with uv or pip:

```console
# Install as a global tool using uv:
$ uv tool install pyproject-runner

# Install in a pyproject-managed virtualenv:
$ uv pip install pyproject-runner

# Install using pip:
$ pip install pyproject-runner
```

This installs the pyproject-runner package along with the `rr` script that is used to execute tasks. When
using pyproject-runner with uv, it is best to install it using the first method so that tasks can be run
by directly calling `rr` rather than the more verbose `uv run rr`.

### Requirements
pyproject-runner requires Python 3.10 or higher because it makes use of [structural pattern matching](https://docs.python.org/3/reference/compound_stmts.html#the-match-statement)
for parsing the pyproject.toml file.


## Usage

Define tasks in the *pyproject.toml* file:

```toml
[tool.pyproject-runner.tasks]
devserver = "flask run --app ./hello.py --debug"
http = { cmd = ["python", "-mhttp.server", "8000"] }
lint = { chain = ["lint:black", "lint:flake8" ] }
"lint:black" = "uvx black --check src"
"lint:flake8" = "uvx flake8 src"
"lint:ruff" = "uvx ruff check src"
ci = "uv run scripts/ci-build.py"  # run script in managed venv using inline script metadata
```

Then execute the tasks using the `rr` command:

```console
$ rr devserver 

# Pass additional arguments to the task
$ rr lint:ruff --show-fixes --statistics
```


## Configuration

Tasks are configured in the `tool.pyproject-runner-tasks` table in the `pyproject.toml` file.

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

Using a table allows for additional configuration using following keys:

#### `cmd`

The command to execute. This is either a `string` or an `array` of arguments. It is executed
directly without a shell, so shell-specific semantics are not directly supported. If the command
contains no path separator (`/` on POSIX or `\` on Windows), the command will first be searched for
in the virtualenv scripts directory, followed by directories in the *PATH* environment variable. If
the command path is absolute, the absolute path will be used. Relative command paths are found
relative to the directory containing the *pyproject.toml* file.

```toml
[tool.pyproject-runner.tasks]
devserver = { cmd = "flask run --app ./hello.py --debug" }
http = { cmd = ["python", "-mhttp.server", "8000"] }
```

#### `chain`

This is a special key that can be used instead of `cmd` to make a task invoke multiple other tasks.
The value is an *array* of task names that will be executed in order, one after another, stopping
after all are complete or when a task fails. All other keys below are ignored by `chain` tasks.

```toml
[tool.pyproject-runner.tasks]
lint = { chain = ["lint:black", "lint:flake8" ] }
"lint:black" = "black --check src"
"lint:flake8" = "flake8 src"
```

#### `cwd`

Commands execute in the current directory by default. Set `cwd` to a *string* to change the working
directory before executing the command. Unless the path is absolute, it is considered relative to
the directory containing the *pyproject.toml* file. The initial working directory is saved in the
*INITIAL_DIR* environment variable.

```toml
[tool.pyproject-runner.tasks]
# Make sure tool can execute from subdirectories in the project
tool = { cmd = "uv run tools/tool.py", cwd = "." }
```

#### `env`

This key can be used to set environment variables before executing a task. It can be a *table* or a
*string*. If a *string* is provided, the value is processed as if read from a file like
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
them directly. Unless the path is absolute, the file is found relative to the directory containing
the *pyproject.toml* file. See [Environment file syntax](#environment-file-syntax) below.

```toml
[tool.pyproject-runner.tasks]
devserver = { cmd = "flask run --debug", env-file = ".dev.env" }
```

#### `help`

A *string* with a help message describing what the task does. It is currently unused by `rr`, but
can be parsed by other tools to display help.

```toml
[tool.pyproject-runner.tasks]
devserver = { cmd = "flask run --app ./hello.py --debug", help = "Start a development server in debug mode" }
```

### Execution environment

Several environment variables are set before executing tasks or processing `env-file` files. Paths
are absolute unless otherwise specified.

VIRTUAL_ENV
: Root of the project's virtual environment.

VIRTUAL_ENV_BIN
: Directory in the project's virtual environment containing the python executable and scripts.

INITIAL_DIR
: Current working directory when pyproject-runner was executed.

PROJECT_DIR
: Directory where the *pyproject.toml* file was found.

WORKSPACE_DIR
: Workspace root, if the project is part of a workspace; otherwise it is unset.

PATH
: Set or modified so that `$VIRTUAL_ENV_BIN` is the first path.

Also, `PYTHONHOME` is removed from the environment if it is set.


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


## Differences from `rye run`

While pyproject-runner started as a feature-parity re-implementation of `rye run` (hence the `rr` script
name), it was also intended as a project to experiment with new features and fixing problems with
`rye run`. It was never intended that it would maintain feature-parity forever. This is especially
true now that development of Rye has stopped. Here are some of the differences for those coming to
pyproject-runner from Rye.

#### Call task type is unsupported

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

#### Relative task commands

Task commands containing path separators are found relative to the directory containing the
*pyproject.toml* file, while `rye run` tries to find scripts relative to the current working
directory.


## Future features

Below is a list of features that might be implemented in the future (no guarantees on any of them).

 - [ ] Task groups (group tasks under common parent command, similar to click)
 - [ ] Markers for platform-specific commands, similar to Python requirements (e.g., `sys.platform == 'win32'`)
 - [ ] Run tasks in parent workspace from child project
 - [ ] Maybe allow passing arguments to tasks of chained tasks?
 - [ ] Task aliases
 - [ ] Add option to show task help
 - [ ] Shell completion
 - [ ] Define common environment variables in [tool.pyproject-runner.environment]?
 - [ ] Environment variable expansion in task definitions
 - [ ] Add an option to create shims for tasks

Do you have additional feature requests? Submit an issue or pull request.

## License

pyproject-runner is licensed under a [3-Clause BSD licence](LICENSE.txt).
