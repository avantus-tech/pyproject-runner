from __future__ import annotations

import os
import pathlib
import re
import shlex
import subprocess
import sys
from typing import Any, Final, Mapping, overload, Sequence

from . import environment
from . import _project


# Paths are manipulated as strings using os.path, rather than pathlib, because a leading ./ is
# significant, and pathlib removes them when normalizing. This is especially important in commands
# as the leading ./ is needed to execute commands in the current directory.

SEP: Final = r'[\\/]' if sys.platform == 'win32' else '/'
ROOT_TEST: Final = re.compile(rf'^!(?:{SEP}|$)')


def build_path(path: str | None, parent: str | pathlib.Path) -> str | None:
    """Perform common path manipulations.

    If path is empty or None, return None. If it starts with a bang (!), return
    the path relative to the parent. Otherwise, just return the path.
    """
    if not path:
        return None
    elif match := ROOT_TEST.match(path):
        return os.path.join(parent, path[match.regs[0][1]:])
    return path


class Task:
    __slots__ = 'cmd', 'cwd', 'env', 'env_file', 'help', 'executable', 'pre_tasks', 'post_tasks'

    @overload
    def __init__(self, cmd: str, *, executable: str) -> None: ...
    @overload
    def __init__(self, cmd: str | Sequence[str] | None, *, cwd: str | None = None,
                 env: str | Mapping[str, str] | None = None, env_file: str | None = None,
                 help: str | None = None, pre_tasks: Sequence[str] | None = None,
                 post_tasks: Sequence[str] | None = None) -> None: ...

    def __init__(self, cmd: str | Sequence[str] | None, *, cwd: str | None = None,
                 env: str | Mapping[str, str] | None = None, env_file: str | None = None,
                 help: str | None = None, executable: str | None = None,
                 pre_tasks: Sequence[str] | None = None,
                 post_tasks: Sequence[str] | None = None) -> None:
        if isinstance(cmd, str):
            cmd = shlex.split(cmd)
        self.cmd: Final = tuple(cmd) if cmd else None
        self.cwd: Final = cwd
        self.env: Final = env
        self.env_file: Final = env_file
        self.help: Final = help
        self.executable: Final = executable
        self.pre_tasks: Final = tuple(pre_tasks) if pre_tasks else ()
        self.post_tasks: Final = tuple(post_tasks) if post_tasks else ()

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, self.__class__) and
                all(getattr(self, name) == getattr(other, name) for name in self.__slots__))

    def __bool__(self) -> bool:
        return not not (self.cmd or self.pre_tasks or self.post_tasks)

    def __repr__(self) -> str:
        args = []
        for name in self.__slots__:
            value = getattr(self, name)
            if value is not None:
                args.append(f'{name}={getattr(self, name)!r}')
        return f'{self.__class__.__module__}.{self.__class__.__qualname__}({", ".join(args)})'

    def to_dict(self) -> dict[str, Any]:
        return {name: value for name in self.__slots__
                if (value := getattr(self, name)) is not None}

    def _get_environment(self, project: _project.PyProject) -> dict[str, str]:
        env = os.environ.copy()
        env['VIRTUAL_ENV'] = str(project.venv_path)
        env['VIRTUAL_ENV_BIN'] = str(project.venv_bin_path)
        env['INITIAL_DIR'] = os.getcwd()
        env['PROJECT_DIR'] = str(project.root)
        workspace = project.workspace
        if workspace is not None:
            env['WORKSPACE_DIR'] = str(workspace.root)
        else:
            env.pop('WORKSPACE_DIR', None)
        try:
            path = env['PATH']
        except KeyError:
            env['PATH'] = str(project.venv_bin_path)
        else:
            env['PATH'] = f'{project.venv_bin_path}{os.pathsep}{path}'
        if isinstance(self.env, str):
            env = environment.expand(self.env, env)
        elif self.env:
            env.update(self.env)
        env_path = build_path(self.env_file, project.root)
        if env_path:
            with open(env_path, encoding='utf-8') as file:
                text = file.read()
            env = environment.expand(text, env)
        env.pop('PYTHONHOME', None)
        return env

    def run(self, args: Sequence[str], project: _project.PyProject) -> int:
        # Look up tasks before attempting to run them
        pre_tasks = [project.task(name) for name in self.pre_tasks] if self.pre_tasks else None
        post_tasks = [project.task(name) for name in self.post_tasks] if self.post_tasks else None

        # Execute pre-tasks, then this task, followed by post-tasks, stopping on any error
        returncode = 0
        if pre_tasks:
            returncode = self._run_tasks(pre_tasks, project)
        if not returncode and self.cmd:
            returncode = self._run(args, project)
        if post_tasks and not returncode:
            returncode = self._run_tasks(post_tasks, project)
        return returncode

    def _run(self, args: Sequence[str], project: _project.PyProject) -> int:
        assert self.cmd
        if self.executable:
            args = [*self.cmd, *args]
        else:
            exe, *args = args
            path = build_path(exe, project.root)
            assert path
            args = [path, *args]
        cwd = build_path(self.cwd, project.root)
        env = self._get_environment(project)
        return subprocess.run(args, cwd=cwd, env=env, executable=self.executable).returncode

    @staticmethod
    def _run_tasks(tasks: Sequence[Task], project: _project.PyProject) -> int:
        for task in tasks:
            returncode = task.run((), project)
            if returncode:
                return returncode
        return 0

    @classmethod
    def parse(cls, entry: str | Sequence[str] | Mapping[str, Any]) -> Task:
        cmd: str | list[str] | None
        match entry:
            case str(cmd) if cmd := cmd.strip():
                return Task(cmd)
            case [*cmd] if cmd and all(isinstance(s, str) for s in cmd) and (name := cmd[0].strip()):
                cmd[0] = name
                return Task(cmd)
            case {"cmd": str(cmd)} if cmd := cmd.strip():
                pass
            case {"cmd": [*cmd]} if cmd and all(isinstance(v, str) for v in cmd) and (name := cmd[0].strip()):
                cmd[0] = name
            case _:
                cmd = None

        # Match options
        cwd: str | None = None
        env_file: str | None = None
        env: str | dict[str, str] | None = None
        if cmd:
            match entry:
                case {"cwd": str(cwd)} if cwd := cwd.strip():
                    pass
                case _:
                    cwd = None

            match entry:
                case {"env": str(env)} if env := env.strip():
                    pass
                case {"env": {**table}} if env := {k: v for k, v in table.items()
                                                   if isinstance(k, str) and isinstance(v, str)}:
                    pass
                case _:
                    env = None

            match entry:
                case {"env-file": str(env_file)} if env_file := env_file.strip():
                    pass
                case _:
                    env_file = None

        help: str | None
        match entry:
            case {"help": str(help)} if help := help.strip():
                pass
            case _:
                help = None

        pre_tasks: list[str] | None
        match entry:
            case {"pre": [*pre_tasks]} if pre_tasks and all(
                    isinstance(v, str) for v in pre_tasks):
                pass
            case _:
                pre_tasks = None

        post_tasks: list[str] | None
        match entry:
            case {"post": [*post_tasks]} if post_tasks and all(
                    isinstance(v, str) for v in post_tasks):
                pass
            case _:
                post_tasks = None

        return cls(cmd, cwd=cwd, env=env, env_file=env_file, help=help,
                   pre_tasks=pre_tasks, post_tasks=post_tasks)
