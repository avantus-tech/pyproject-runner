from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
from typing import Literal

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

from collections.abc import Iterator, Mapping, Sequence
from typing import Any, Final, Protocol, overload

from . import environment

# Paths associated with tasks are manipulated as strings using os.path, rather than pathlib,
# because a leading ./ is significant, and pathlib removes it when normalizing. This is especially
# important in commands as the leading ./ is needed to execute commands in the current directory.

if sys.platform == "win32":
    VENV_BIN: Final = "Scripts"
    SEP: Final = r"[\\/]"

    def external_scripts(path: Path) -> Iterator[str]:
        return (path.stem for path in path.iterdir()
                if path.is_file() and path.suffix.lower() in {".exe", ".bat"}
                and not is_unsafe_script(path))

    def is_unsafe_script(path: Path) -> bool:
        return path.stem in {"activate", "deactivate"}
else:
    VENV_BIN: Final = "bin"
    SEP: Final = "/"

    def external_scripts(path: Path) -> Iterator[str]:
        return (path.name for path in path.iterdir()
                if path.is_file() and os.access(path, os.X_OK)
                and not is_unsafe_script(path))

    def is_unsafe_script(path: Path) -> bool:
        return path.suffix in {".dylib", ".so"}


ROOT_TEST: Final = re.compile(rf"^!(?:{SEP}|$)")


def build_path(path: str | None, parent: str | Path) -> str | None:
    """Perform common path manipulations.

    If path is empty or None, return None. If it starts with a bang (!), return
    the path relative to the parent. Otherwise, just return the path.
    """
    if not path:
        return None
    if match := ROOT_TEST.match(path):
        return str(Path(parent, path[match.regs[0][1]:]))
    return path


class TaskError(Exception):
    pass


class Project(Protocol):
    @property
    def doc(self) -> Mapping[str, Any]: ...

    @property
    def task_names(self) -> list[str]:
        return list(self._tasks())

    def task(self, name: str) -> Task:
        tasks = self._tasks()
        entry = tasks.get(name)
        if entry:
            try:
                return Task.parse(entry)
            except ValueError:
                raise TaskError(f"{name!r} task definition is invalid")
        else:
            executable = shutil.which(name, path=self.venv_bin_path)
            if executable and not is_unsafe_script(Path(executable)):
                return Task(name, executable=executable)
        raise TaskError(f"{name!r} task is not defined")

    def _tasks(self) -> Mapping[str, Any]:
        match self.doc:
            case {"tool": {"pyproject-runner": {"tasks": dict(tasks)}}}:
                return tasks
        return {}

    @property
    def root(self) -> Path: ...

    @property
    def venv_path(self) -> Path:
        project_venv: str | None
        match self.doc:
            case {"tool": {"uv": {"managed": False}}}:
                project_venv = os.environ.get("VIRTUAL_ENV")
            case _:
                project_venv = os.environ.get("UV_PROJECT_ENVIRONMENT")
        if project_venv:
            return Path(project_venv)
        return self.root / ".venv"

    @property
    def venv_bin_path(self) -> Path:
        return self.venv_path / VENV_BIN

    @property
    def venv_python_bin(self) -> Path:
        path = self.venv_bin_path / "python"
        if sys.platform == "win32":
            path = path.with_suffix(".exe")
        return path


class PyProject(Project):
    __slots__ = "_workspace", "doc", "name", "root"

    def __init__(self, name: str, root: Path, doc: Mapping[str, Any]) -> None:
        self.name: Final = name
        self.root: Final = root
        self.doc: Final = doc
        self._workspace: Workspace | None

    @classmethod
    def from_project_document(cls, document: Mapping[str, Any], root: Path) -> PyProject:
        match document:
            case {"project": {"name": str(name)}}:
                return cls(name, root, doc=document)
        raise ValueError("Invalid python project document")

    def __repr__(self) -> str:
        args = f"name={self.name!r}, root={self.root!r}, doc={self.doc!r}"
        return f"{self.__class__.__module__}.{self.__class__.__qualname__}({args})"

    @classmethod
    def discover(cls, path: Path) -> PyProject | None:
        while True:
            project_file = path / "pyproject.toml"
            if project_file.is_file():
                try:
                    return cls.load(project_file)
                except ValueError:
                    pass
            parent = path.parent
            if path == parent:
                return None  # No more directories to traverse
            path = parent

    @classmethod
    def load(cls, project_file: Path) -> PyProject:
        with project_file.open("rb") as file:
            doc = tomllib.load(file)
        return cls.from_project_document(doc, project_file.parent)

    @property
    def workspace(self) -> Workspace | None:
        try:
            return self._workspace
        except AttributeError:
            pass
        workspace = Workspace.from_pyproject(self) or self.discover_workspace()
        self._workspace = workspace
        return workspace

    def discover_workspace(self) -> Workspace | None:
        path = self.root
        while True:
            parent = path.parent
            if path == parent:
                return None  # No more directories to traverse
            project = self.discover(parent)
            if project is None:
                return None
            path = project.root
            workspace = Workspace.from_pyproject(project)
            if workspace is not None:
                for member in workspace.members:
                    if self.root.samefile(member):
                        return workspace

    @property
    def venv_path(self) -> Path:
        workspace = self.workspace
        if workspace is None:
            return super().venv_path
        return workspace.venv_path


class Workspace(Project):
    __slots__ = "doc", "members", "name", "root"

    def __init__(self, name: str, root: Path,
                 doc: Mapping[str, Any], members: Sequence[Path]) -> None:
        self.name: Final = name
        self.root: Final = root
        self.doc: Final = doc
        self.members: Final = members

    def __repr__(self) -> str:
        args = f"name={self.name!r}, root={self.root!r}, doc={self.doc!r}, members={self.members!r}"
        return f"{self.__class__.__module__}.{self.__class__.__qualname__}({args})"

    @classmethod
    def from_pyproject(cls, project: PyProject) -> Workspace | None:
        root = project.root
        match project.doc:
            case {"tool": {"uv": {"workspace": {**workspace}}}}:
                pass
            case _:
                return None
        match workspace:
            case {"members": [*mems]}:
                members = tuple(path for mem in mems if isinstance(mem, str)
                                for path in root.glob(mem))
            case _:
                return None
        match workspace:
            case {"exclude": [*exclusions]}:
                exclude = {path for mem in exclusions if isinstance(mem, str)
                           for path in root.glob(mem)}
            case _:
                exclude = set()
        members = tuple(m for m in members if m not in exclude)
        return cls(project.name, project.root, project.doc, members)


class Task:
    __slots__ = "cmd", "cwd", "env", "env_file", "executable", "help", "post", "pre"

    @overload
    def __init__(self, cmd: str, *, executable: str) -> None: ...
    @overload
    def __init__(self, cmd: str | Sequence[str] | None, *, cwd: str | None = None,
                 env: str | Mapping[str, str] | None = None,
                 env_file: str | Sequence[str] | None = None,
                 help: str | None = None,
                 pre: Sequence[Sequence[str]] | None = None,
                 post: Sequence[Sequence[str]] | None = None) -> None: ...

    def __init__(self, cmd: str | Sequence[str] | None, *, cwd: str | None = None,
                 env: str | Mapping[str, str] | None = None,
                 env_file: str | Sequence[str] | None = None,
                 help: str | None = None,  # noqa: A002
                 executable: str | None = None,
                 pre: Sequence[Sequence[str]] | None = None,
                 post: Sequence[Sequence[str]] | None = None) -> None:
        if isinstance(cmd, str):
            cmd = shlex.split(cmd)
        self.cmd: Final = tuple(cmd) if cmd else None
        self.cwd: Final = cwd
        self.env: Final = env
        self.env_file: Final = env_file
        self.help: Final = help
        self.executable: Final = executable
        self.pre: Final = tuple(tuple(i) for i in pre) if pre else None
        self.post: Final = tuple(tuple(i) for i in post) if post else None

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, self.__class__) and
                all(getattr(self, name) == getattr(other, name) for name in self.__slots__))

    def __repr__(self) -> str:
        args = []
        for name in self.__slots__:
            value = getattr(self, name)
            if value is not None:
                args.append(f"{name}={getattr(self, name)!r}")
        return f'{self.__class__.__module__}.{self.__class__.__qualname__}({", ".join(args)})'

    def to_dict(self) -> dict[str, Any]:
        def convert(value: Any) -> Any:
            match value:
                case str():
                    pass
                case Mapping():
                    value = {k: convert(v) for k, v in value.items()}
                case Sequence():
                    value = [convert(v) for v in value]
            return value

        return {name.replace("_", "-"): convert(value)
                for name in self.__slots__
                if (value := getattr(self, name)) is not None}

    def _get_environment(self, project: PyProject, name: str) -> dict[str, str]:
        env = os.environ.copy()
        env["VIRTUAL_ENV"] = str(project.venv_path)
        env["VIRTUAL_ENV_BIN"] = str(project.venv_bin_path)
        env["INITIAL_DIR"] = str(Path.cwd())
        env["PROJECT_DIR"] = str(project.root)
        workspace = project.workspace
        if workspace is not None:
            env["WORKSPACE_DIR"] = str(workspace.root)
        else:
            env.pop("WORKSPACE_DIR", None)
        try:
            path = env["PATH"]
        except KeyError:
            env["PATH"] = str(project.venv_bin_path)
        else:
            env["PATH"] = f"{project.venv_bin_path}{os.pathsep}{path}"
        env = self.expand_environment(project, name, env)
        env.pop("PYTHONHOME", None)
        return env

    def expand_environment(self, project: PyProject,
                           name: str, env: Mapping[str, str]) -> dict[str, str]:
        if isinstance(self.env, str):
            try:
                expanded_env = environment.expand(self.env, env)
            except SyntaxError:
                raise TaskError(f"Failed to process 'env' value from {name!r} task")
        else:
            expanded_env = dict(env)
            if self.env:
                expanded_env.update(self.env)

        match self.env_file:
            case str(env_file):
                env_files = [env_file]
            case [*env_files]:
                pass
            case _:
                env_files = []

        env_path: str | None
        for env_path in env_files:
            env_path = build_path(env_path, project.root)
            if env_path:
                try:
                    with Path(env_path).open(encoding="utf-8") as file:
                        expanded_env = environment.expand(file.read(), expanded_env)
                except (OSError, SyntaxError) as exc:
                    exc.filename = env_path
                    raise TaskError(f"Failed to process 'env-file' from {name!r} task")

        return expanded_env

    def run(self, project: PyProject, name: str, args: Sequence[str]) -> int:
        """Run the task, returning the process error code."""
        # Look up tasks before attempting to run them
        pre_tasks = self.resolve_tasks(project, name, "pre")
        post_tasks = self.resolve_tasks(project, name, "post")
        # Execute pre-tasks, then this task, followed by post-tasks, stopping on any error
        returncode = 0
        if pre_tasks:
            returncode = self._run_tasks(project, pre_tasks)
        if not returncode and self.cmd:
            returncode = self._run(project, name, args)
        if post_tasks and not returncode:
            returncode = self._run_tasks(project, post_tasks)
        return returncode

    def resolve_tasks(self, project: PyProject, task_name: str, list_name: Literal["pre", "post"],
                     ) -> list[tuple[str, Task, Sequence[str]]] | None:
        """Resolve pre- and post-task names to Task objects."""
        task_list: Sequence[Sequence[str]] | None = getattr(self, list_name)
        if task_list:
            try:
                return [(name, project.task(name), args) for name, *args in task_list]
            except TaskError:
                raise TaskError(f"Failed to resolve a {list_name!r} task from {task_name!r} task")
        return None

    def _run(self, project: PyProject, name: str, args: Sequence[str]) -> int:
        assert self.cmd  # noqa: S101
        args = [*self.cmd, *args]
        if not self.executable:
            exe = args[0]
            path = build_path(exe, project.root)
            assert path  # noqa: S101
            args[0] = path
        cwd = build_path(self.cwd, project.root)
        env = self._get_environment(project, name)
        return subprocess.run(args, cwd=cwd, env=env, executable=self.executable).returncode  # noqa: S603

    @staticmethod
    def _run_tasks(project: PyProject, tasks: Sequence[tuple[str, Task, Sequence[str]]]) -> int:
        for name, task, args in tasks:
            returncode = task.run(project, name, args)
            if returncode:
                return returncode
        return 0

    @classmethod
    def parse(cls, entry: str | Sequence[str] | Mapping[str, Any]) -> Task:
        cmd: str | list[str] | None
        match entry:
            case str(cmd) | {"cmd": str(cmd)}:
                if not cmd or cmd.isspace():
                    raise ValueError(f"Invalid 'cmd' value: {cmd!r}")
            # ignore "Alternative patterns bind different names"
            case [*cmd] | {"cmd": [*cmd]}:  # type: ignore[misc]
                if not (cmd and all(isinstance(s, str)
                                    for s in cmd) and cmd[0] and not cmd[0].isspace()):
                    raise ValueError(f"Invalid 'cmd' value: {cmd!r}")
            case {"cmd": value}:
                raise ValueError(f"Invalid 'cmd' value: {value!r}")
            case _:
                cmd = None

        if not isinstance(entry, Mapping):
            return Task(cmd)

        # Match cmd options
        if cmd:
            match entry.get("cwd"):
                case str(cwd) if cwd and not cwd.isspace():
                    pass
                case None as cwd:
                    pass
                case value:
                    raise ValueError(f"Invalid 'cwd' value: {value!r}")

            env: str | dict[str, str] | None
            match entry.get("env"):
                case str(env) if env or not env.isspace():
                    pass
                case dict(table) if (all(isinstance(k, str) and
                                       isinstance(v, str) for k, v in table.items())):
                    env = table
                case None as env:
                    pass
                case value:
                    raise ValueError(f"Invalid 'env' value: {value!r}")

            match entry.get("env-file"):
                case str(env_file) if env_file and not env_file.isspace():
                    pass
                case [*env_file] if (env_file and all(isinstance(v, str) and v and
                                                      not v.isspace() for v in env_file)):
                    pass
                case None as env_file:
                    pass
                case value:
                    raise ValueError(f"Invalid 'env-file' value: {value!r}")
        else:
            cwd = env = env_file = None

        match entry.get("help"):
            case str(help) if help and not help.isspace():
                pass
            case None as help:
                pass
            case value:
                raise ValueError(f"Invalid 'help' value: {value!r}")

        match entry.get("pre"):
            case [*tasks]:
                try:
                    pre_tasks = cls._parse_tasks(tasks)
                except ValueError as exc:
                    raise ValueError(f"Invalid 'pre' task value: {exc}") from None
            case None as pre_tasks:
                pass
            case value:
                raise ValueError(f"Invalid 'pre' value: {value!r}")

        match entry.get("post"):
            case [*tasks]:
                try:
                    post_tasks = cls._parse_tasks(tasks)
                except ValueError as exc:
                    raise ValueError(f"Invalid 'post' task value: {exc}") from None
            case None as post_tasks:
                pass
            case value:
                raise ValueError(f"Invalid 'post' value: {value!r}")

        if not (cmd or pre_tasks or post_tasks):
            raise ValueError("Task must define at least one of 'cmd', 'pre', or 'post'")

        return cls(cmd, cwd=cwd, env=env, env_file=env_file, help=help,
                   pre=pre_tasks, post=post_tasks)

    @staticmethod
    def _parse_tasks(tasks: list[Any]) -> list[list[str]] | None:
        name: str
        args: list[str]
        task_list: list[list[str]] = []
        for task in tasks:
            match task:
                case str() if task and not task.isspace():
                    task_list.append(shlex.split(task))
                case [str(name), *args] if (name and not name.isspace() and
                                            all(isinstance(a, str) for a in args)):
                    task_list.append(task)
                case _:
                    raise ValueError(repr(task))
        return task_list or None
