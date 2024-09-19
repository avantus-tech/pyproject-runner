from __future__ import annotations

import os
from pathlib import Path
import shlex
import subprocess
import sys
from typing import Any, cast, Final, Iterator, Mapping, Sequence

from . import environment
from . import _project


class RunError(Exception):
    pass


class _Base:
    __slots__ = 'cwd', 'env', 'env_file', 'help'
    __match_args__ = 'cwd', 'env', 'env_file', 'help'

    def __init__(self, cwd: str | None = None, env: str | Mapping[str, str] | None = None,
                 env_file: str | None = None, help: str | None = None) -> None:
        self.cwd: Final = cwd
        self.env: Final = env
        self.env_file: Final = env_file
        self.help: Final = help

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, self.__class__) and
                self.cwd == other.cwd and self.env == other.env and
                self.env_file == other.env_file and self.help == other.help)

    def __repr__(self) -> str:
        args = self._format_args()
        return f'{self.__class__.__module__}.{self.__class__.__qualname__}({args})'

    def _format_args(self) -> str:
        return f'cwd={self.cwd!r}, env={self.env!r}, env_file={self.env_file!r}, help={self.help!r}'

    def to_dict(self) -> dict[str, Any]:
        return {key: value for key, value in [
            ('cwd', self.cwd),
            ('env', self.env),
            ('env-file', self.env_file),
            ('help', self.help),
        ] if value is not None}

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
        if self.env_file:
            env_path = Path(self.env_file)
            if not env_path.is_absolute():
                env_path = project.root / env_path
            env = environment.expand(env_path.read_text('utf-8'), env)
        env.pop('PYTHONHOME', None)
        return env

    def _run(self, args: Sequence[str | Path], project: _project.PyProject, *,
             executable: str | os.PathLike[str] | None = None) -> int:
        if executable is None:
            exe = str(args[0])
            # pathlib is not used here because it drops './' from paths
            if os.sep in exe and not os.path.isabs(exe):
                args = [project.root / exe, *args[1:]]
        if self.cwd and not os.path.isabs(self.cwd):
            cwd: str | Path | None = project.root / self.cwd
        else:
            cwd = self.cwd
        env = self._get_environment(project)
        return subprocess.run(args, cwd=cwd, env=env, executable=executable).returncode


class Cmd(_Base):
    __slots__ = 'cmd',
    __match_args__ = 'cmd', 'cwd', 'env', 'env_file', 'help'

    def __init__(self, cmd: str | Sequence[str], cwd: str | None = None,
                 env: str | Mapping[str, str] | None = None, env_file: str | None = None,
                 help: str | None = None) -> None:
        if isinstance(cmd, str):
            cmd = shlex.split(cmd)
        self.cmd: Final = tuple(cmd)
        super().__init__(cwd=cwd, env=env, env_file=env_file, help=help)

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, self.__class__) and self.__class__ == other.__class__ and
                self.cmd == other.cmd and super().__eq__(other))

    def _format_args(self) -> str:
        return f'cmd={self.cmd!r}, {super()._format_args()}'

    def to_dict(self) -> dict[str, Any]:
        return {'cmd': self.cmd} | super().to_dict()

    def run(self, args: Sequence[str | Path], project: _project.PyProject) -> int:
        return super()._run([*self.cmd, *args], project)


class Chain(_Base):
    __slots__ = 'chain',
    __match_args__ = 'chain', 'cwd', 'env', 'env_file', 'help'

    def __init__(self, chain: Sequence[str], cwd: str | None = None,
                 env: str | Mapping[str, str] | None = None, env_file: str | None = None,
                 help: str | None = None) -> None:
        self.chain: Final = tuple(chain)
        super().__init__(cwd=cwd, env=env, env_file=env_file, help=help)

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, self.__class__) and self.__class__ == other.__class__ and
                self.chain == other.chain and super().__eq__(other))

    def _format_args(self) -> str:
        return f'chain={self.chain!r}, {super()._format_args()}'

    def to_dict(self) -> dict[str, Any]:
        return {'chain': self.chain} | super().to_dict()

    def run(self, args: Sequence[str | Path], project: _project.PyProject) -> int:
        if args:
            raise RunError('extra arguments to chained commands are not allowed')
        for name in self.chain:
            returncode = run_script(project, name, ())
            if returncode:
                return returncode
        return 0


class External(_Base):
    __slots__ = 'cmd', 'executable'
    __match_args__ = 'cmd', 'executable'

    def __init__(self, cmd: str, executable: str | Path) -> None:
        self.cmd: Final = cmd
        self.executable: Final = executable
        super().__init__()

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, self.__class__) and self.__class__ == other.__class__ and
                self.cmd == other.cmd)

    def _format_args(self) -> str:
        return f'cmd={self.cmd!r}'

    def to_dict(self) -> dict[str, Any]:
        return {}

    def run(self, args: Sequence[str | Path], project: _project.PyProject) -> int:
        return super()._run([self.cmd, *args], project, executable=self.executable)


ScriptType = Cmd | Chain | External


if sys.platform == 'win32':
    def external_scripts(path: Path) -> Iterator[str]:
        return (path.stem for path in path.iterdir()
                if path.is_file() and path.suffix.lower() in ('.exe', '.bat')
                and not is_unsafe_script(path))

    def is_unsafe_script(path: Path) -> bool:
        return path.stem in {'activate', 'deactivate'}
else:
    def external_scripts(path: Path) -> Iterator[str]:
        return (path.name for path in path.iterdir()
                if path.is_file() and os.access(path, os.X_OK)
                and not is_unsafe_script(path))

    def is_unsafe_script(path: Path) -> bool:
        return path.suffix == '.dylib'


def parse_script(entry: str | Sequence[str] | Mapping[str, Any]) -> ScriptType | None:
    # Match the script type for non-dict-based scripts
    match entry:
        case str(cmd) if cmd := cmd.strip():
            return Cmd(cmd)
        case [*cmd] if cmd and all(isinstance(s, str) for s in cmd) and cmd[0].strip():
            cmd[0] = cmd[0].strip()
            return Cmd(cmd)
        case {}:
            pass  # dict-based scripts are handled below
        case _:
            return None

    # Match options
    cwd: str | None
    match entry:
        case {"cwd": str(cwd)} if cwd:
            pass
        case _:
            cwd = None

    env: str | Mapping[str, str] | None
    match entry:
        case {"env": str(env)} if env := str(env).strip():
            pass
        case {"env": {**table}} if table and all(isinstance(v, str) for v in table.values()):
            env = cast(Mapping[str, str], table)
        case _:
            env = None

    env_file: str | None
    match entry:
        case {"env-file": str(env_file)} if env_file:
            pass
        case _:
            env_file = None

    help: str | None
    match entry:
        case {"help": str(help)} if help:
            pass
        case _:
            help = None

    # Match the script type
    string: str
    seq: Sequence[str]
    match entry:
        case {"chain": [*seq]} if seq and all(isinstance(v, str) for v in seq) and (seq := [v for v in seq if v]):
            return Chain(seq, cwd=cwd, env=env, env_file=env_file, help=help)
        case {"cmd": str(string)} if string := string.strip():
            try:
                seq = shlex.split(string)
            except ValueError:
                return None
            return Cmd(seq, cwd=cwd, env=env, env_file=env_file, help=help)
        case {"cmd": [str(string), *seq]} if all(isinstance(v, str) for v in seq) and (string := string.strip()):
            return Cmd([string, *seq], cwd=cwd, env=env, env_file=env_file, help=help)

    return None


def run_script(project: _project.PyProject, name: str, args: Sequence[str]) -> int:
    script = project.script(name)
    if script is None:
        raise RunError(f'invalid or unknown script {name!r}')
    return script.run(args, project)
