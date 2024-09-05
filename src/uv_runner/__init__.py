from __future__ import annotations

import os
from pathlib import Path
import re
import shlex
import shutil
import subprocess
import sys
if sys.version_info < (3, 11):
    import tomli as tomllib
else:
    import tomllib
from typing import Any, cast, Final, Iterator, Mapping, NoReturn, overload, Protocol, Sequence

import click

from . import environment


VENV_BIN: Final = "Scripts" if sys.platform == "win32" else "bin"
DOTNAME: Final = r'[a-zA-Z_]\w*(?:\.[a-zA-Z_]\w*)*'
CALL_REGEX: Final = re.compile(rf'{DOTNAME}(?::{DOTNAME}(?:\(.*\))?)?$')


class _ScriptBase:
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

    def _get_environment(self, project: PyProject) -> dict[str, str]:
        env = os.environ.copy()
        env['VIRTUAL_ENV'] = str(project.venv_path)
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
        if self.env:
            if isinstance(self.env, str):
                env = environment.load_environment(self.env, env)
            else:
                env.update(self.env)
        if self.env_file:
            path = Path(self.env_file)
            if not path.is_absolute():
                path = project.root / path
            env = environment.load_environment_file(path, env)
        env.pop('PYTHONHOME', None)
        return env

    def run(self, args: Sequence[str | Path], project: PyProject, *,
            executable: str | os.PathLike[str] | None = None) -> int:
        if executable is None:
            exe = args[0]
            if os.sep in exe and not os.path.isabs(exe):
                args = [project.root / exe, args[1:]]
        cwd = self.cwd
        if cwd and not os.path.isabs(cwd):
            cwd = project.root / cwd
        env = self._get_environment(project)
        return subprocess.run(args, cwd=cwd, env=env, executable=executable).returncode


class Cmd(_ScriptBase):
    __slots__ = 'cmd',
    __match_args__ = 'cmd', 'cwd', 'env', 'env_file', 'help'

    def __init__(self, cmd: str | Sequence[str], cwd: str | None = None,
                 env: Mapping[str, str] | None = None, env_file: str | None = None,
                 help: str | None = None) -> None:
        if isinstance(cmd, str):
            cmd = shlex.split(cmd)
        self.cmd: Final = cmd
        super().__init__(cwd=cwd, env=env, env_file=env_file, help=help)

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, self.__class__) and self.__class__ == other.__class__ and
                self.cmd == other.cmd and super().__eq__(other))

    def _format_args(self) -> str:
        return f'cmd={self.cmd!r}, {super()._format_args()}'

    def to_dict(self) -> dict[str, Any]:
        return {'cmd': self.cmd} | super().to_dict()

    def run(self, args: Sequence[str | Path], project: PyProject) -> int:
        return super().run([*self.cmd, *args], project)


class Call(_ScriptBase):
    __slots__ = 'call',
    __match_args__ = 'call', 'cwd', 'env', 'env_file', 'help'

    def __init__(self, call: str, cwd: str | None = None,
                 env: Mapping[str, str] | None = None, env_file: str | None = None,
                 help: str | None = None) -> None:
        self.call: Final = call
        super().__init__(cwd=cwd, env=env, env_file=env_file, help=help)

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, self.__class__) and self.__class__ == other.__class__ and
                self.call == other.call and super().__eq__(other))

    def _format_args(self) -> str:
        return f'call={self.call!r}, {super()._format_args()}'

    def to_dict(self) -> dict[str, Any]:
        return {'call': self.call} | super().to_dict()

    def run(self, args: Sequence[str | Path], project: PyProject) -> int:
        cmd: list[str | Path] = ['python']
        try:
            module, call = self.call.split(':', maxsplit=1)
        except ValueError:
            cmd += ['-m', self.call]
        else:
            if '(' not in call:
                call = f'{call}()'
            cmd += ['-c', f"import sys, {module} as _1; sys.exit(_1.{call})"]
        cmd += args
        return super().run(cmd, project, executable=project.venv_python_bin)


class Chain(_ScriptBase):
    __slots__ = 'chain',
    __match_args__ = 'chain', 'cwd', 'env', 'env_file', 'help'

    def __init__(self, chain: Sequence[str], cwd: str | None = None,
                 env: Mapping[str, str] | None = None, env_file: str | None = None,
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

    def run(self, args: Sequence[str | Path], project: PyProject) -> int:
        raise NotImplementedError('Chain commands cannot be run directly')


class Exec(_ScriptBase):
    __slots__ = 'exec',
    __match_args__ = 'exec', 'cwd', 'env', 'env_file', 'help'

    def __init__(self, exec: str, cwd: str | None = None,
                 env: Mapping[str, str] | None = None, env_file: str | None = None,
                 help: str | None = None) -> None:
        self.exec: Final = exec
        super().__init__(cwd=cwd, env=env, env_file=env_file, help=help)

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, self.__class__) and self.__class__ == other.__class__ and
                self.exec == other.exec and super().__eq__(other))

    def _format_args(self) -> str:
        return f'exec={self.exec!r}, {super()._format_args()}'

    def to_dict(self) -> dict[str, Any]:
        return {'exec': self.exec} | super().to_dict()

    def run(self, args: Sequence[str | Path], project: PyProject) -> int:
        return super().run([project.venv_python_bin, '-c', self.exec, *args], project)


class External(_ScriptBase):
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

    def run(self, args: Sequence[str | Path], project: PyProject) -> int:
        return super().run([self.cmd, *args], project, executable=self.executable)


_Script = Cmd | Call | Chain | Exec | External


class Project(Protocol):
    @property
    def doc(self) -> Mapping[str, Any]: ...

    @property
    def managed(self) -> bool:
        match self.doc:
            case {"tool": {"uv": {"managed": bool(is_managed)}}}:
                return is_managed
        return True

    def script(self, name: str) -> _Script | None:
        scripts = self._scripts()
        entry = scripts.get(name)
        if entry is None:
            executable = shutil.which(name, path=self.venv_bin_path)
            if executable and not is_unsafe_script(Path(executable)):
                return External(name, executable)
            return None
        return self._parse_script(entry)

    def _scripts(self) -> Mapping[str, Any]:
        match self.doc:
            case {"tool": {"uv-runner": {"scripts": {**scripts}}}}:
                return cast(Mapping[str, Any], scripts)
        return {}

    def iter_scripts(self) -> Iterator[tuple[str, _Script]]:
        for name, entry in self._scripts().items():
            script = self._parse_script(entry)
            if script is not None:
                yield name, script

    def _parse_script(self, entry: str | Sequence[str] | Mapping[str, Any]) -> _Script | None:
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
            case {"call": str(string)} if CALL_REGEX.match(string):
                return Call(string, cwd=cwd, env=env, env_file=env_file, help=help)
            case {"chain": [*seq]} if seq and all(isinstance(v, str) for v in seq) and (seq := [v for v in seq if v]):
                return Chain(seq, cwd=cwd, env=env, env_file=env_file, help=help)
            case {"cmd": str(string)} if string := string.strip():
                try:
                    seq = shlex.split(string)
                except ValueError:
                    return None
                return Cmd(seq, cwd=cwd, env=env, env_file=env_file, help=help)
            case {"cmd": [*seq]} if seq and all(isinstance(v, str) for v in seq) and seq[0].strip():
                seq[0] = seq[0].strip()
                return Cmd(seq, cwd=cwd, env=env, env_file=env_file, help=help)
            case {"exec": str(string)} if string := string.strip():
                return Exec(string, cwd=cwd, env=env, env_file=env_file, help=help)

        return None

    def sync(self) -> None:
        subprocess.run(['uv', 'sync', '--directory', self.root, '--frozen'])

    @property
    def root(self) -> Path: ...

    @property
    def venv_path(self) -> Path:
        project_venv = os.environ.get("UV_PROJECT_ENVIRONMENT")
        if project_venv:
            return Path(project_venv)
        return self.root / '.venv'

    @property
    def venv_bin_path(self) -> Path:
        return self.venv_path / VENV_BIN

    @property
    def venv_python_bin(self) -> Path:
        path = self.venv_bin_path / 'python'
        if sys.platform == "win32":
            path = path.with_suffix('.exe')
        return path


class PyProject(Project):
    __slots__ = 'name', 'root', 'doc', '_workspace'
    __match_args__ = 'name', 'root', 'doc'

    def __init__(self, name: str, root: Path, doc: Mapping[str, Any]) -> None:
        self.name: Final = name
        self.root: Final = root
        self.doc: Final = doc
        self._workspace: Workspace | None

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, self.__class__) and self.__class__ == other.__class__ and
                self.root == other.root and self.doc == other.doc)

    def __repr__(self) -> str:
        args = f'root={self.root!r}, doc={self.doc!r}'
        return f'{self.__class__.__module__}.{self.__class__.__qualname__}({args})'

    @property
    def workspace(self) -> Workspace | None:
        try:
            return self._workspace
        except AttributeError:
            pass
        workspace = Workspace.from_pyproject(self)
        if workspace is None:
            workspace = self.discover_workspace()
        self._workspace = workspace
        return workspace

    def discover_workspace(self) -> Workspace | None:
        path = self.root
        while True:
            parent = path.parent
            if path == parent:
                return None
            project = self.discover(parent)
            if project is None:
                return None
            path = project.root
            workspace = Workspace.from_pyproject(project)
            if workspace is None:
                continue
            for member in workspace.members:
                if self.root.samefile(member):
                    return workspace

    @classmethod
    def discover(cls, path: Path) -> PyProject | None:
        while True:
            project_file = path / "pyproject.toml"
            if project_file.is_file():
                project = cls.load(project_file)
                if project is not None:
                    return project
            parent = path.parent
            if path == parent:
                return None
            path = parent

    @classmethod
    def load(cls, project_file: Path) -> PyProject | None:
        with project_file.open('rb') as file:
            doc = tomllib.load(file)
        match doc:
            case {"project": {"name": str(name)}}:
                return cls(name, project_file.parent, doc=doc)
        return None

    @property
    def venv_path(self) -> Path:
        workspace = self.workspace
        if workspace is None:
            return super().venv_path
        return workspace.venv_path


class Workspace(Project):
    __slots__ = 'name', 'root', 'doc', 'members'
    __match_args__ = 'name', 'root', 'doc', 'members'

    def __init__(self, name: str, root: Path, doc: Mapping[str, Any], members: Sequence[Path]) -> None:
        self.name: Final = name
        self.root: Final = root
        self.doc: Final = doc
        self.members: Final = members

    def __eq__(self, other: object) -> bool:
        return (isinstance(other, self.__class__) and self.__class__ == other.__class__ and
                self.root == other.root and self.doc == other.doc)

    def __repr__(self) -> str:
        args = f'root={self.root!r}, doc={self.doc!r}, members={self.members!r}'
        return f'{self.__class__.__module__}.{self.__class__.__qualname__}({args})'

    @classmethod
    def from_pyproject(cls, project: PyProject) -> Workspace | None:
        root = project.root
        match project.doc:
            case {"tool": {"uv": {"workspace": {**workspace}}}}:
                pass
            case _:
                return None
        match workspace:
            case {"members": [*members]}:
                members = tuple(path for mem in members if isinstance(mem, str)
                                for path in root.glob(mem))
            case _:
                return None
        match workspace:
            case {"exclude": [*exclude]}:
                exclude = set(path for mem in exclude if isinstance(mem, str)
                              for path in root.glob(mem))
            case _:
                exclude = set()
        members = tuple(m for m in members if m not in exclude)
        return cls(project.name, project.root, project.doc, members)


@click.command(
    context_settings={
        'allow_interspersed_args': False,
        'max_content_width': 120,
        'help_option_names': ['-h', '--help']
    },
)
@click.option('-l', '--list', 'do_list', is_flag=True, default=False,
              help="List all scripts.")
@click.option("--pyproject", metavar="PATH",
              type=click.Path(exists=True, dir_okay=True, resolve_path=True, path_type=Path),
              help="Use this pyproject.toml file or directory.")
@click.argument('command', metavar="[COMMAND]", nargs=-1)
def main(command: tuple[str, ...], do_list: bool, pyproject: Path | None) -> None:
    """Runs a configured script or a command installed for this package."""
    if pyproject and pyproject.is_file():
        project: PyProject | None = PyProject.load(pyproject)
    else:
        project = PyProject.discover(pyproject or Path().resolve())
    if project is None:
        _error('did not find pyproject.toml')
    if do_list or not command:
        list_scripts(project, True)
        exit(0)
    if project.managed:
        project.sync()
    name, *args = command
    exit(run_script(project, name, args))


def list_scripts(project: PyProject, include_external: bool) -> None:
    scripts: list[tuple[str, _Script | None]] = list(project.iter_scripts())
    if include_external:
        scripts += [(name, None) for name in external_scripts(project.venv_bin_path)]
    scripts.sort(key=lambda c: (c[0], c[1] is None))
    for name, script in scripts:
        if script is None:
            click.echo(name)
        else:
            click.echo(f'{click.style(name, fg="cyan", bold=True)}  {click.style(script.to_dict(), fg="yellow")}')


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


def run_script(project: PyProject, name: str, args: Sequence[str]) -> int:
    script = project.script(name)
    if script is None:
        _error(f'invalid or unknown script {name!r}')
    match script:
        case Chain() if args:
            _error('extra arguments to chained commands are not allowed')
        case Chain(chain):
            for name in chain:
                returncode = run_script(project, name, ())
                if returncode:
                    return returncode
        case Call() | Cmd() | External():
            return script.run(args, project)
        case _:
            raise NotImplementedError
    return 0


@overload
def _error(msg: str, exitcode: int = ...) -> NoReturn: ...
@overload
def _error(msg: str, exitcode: None) -> None: ...
def _error(msg: str, exitcode: int | None = 1) -> NoReturn | None:
    click.echo(f'{click.style("error", fg="red", bold=True)}: {msg}')
    if exitcode is not None:
        exit(exitcode)
    return None
