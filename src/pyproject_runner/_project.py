
# TODO additional features
# - [ ] Command groups (to group commands, similar to click)
# - [ ] Markers for platform-specific commands (e.g., sys.platform == 'win32')
# - [ ] Include tasks from parent workspace
# - [ ] Maybe allow passing arguments to subcommands of chain commands?
# - [ ] Task aliases
# - [ ] Add option to show task help
# - [ ] Shell completion
# - [ ] Define environment variables in [tool.uv-runner.environment]?
# - [ ] Environment variable expansion in commands?
# - [ ] Add a command to create shims to rr tasks

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys
if sys.version_info < (3, 11):
    import tomli as tomllib
else:
    import tomllib
from typing import Any, cast, Final, Iterator, Mapping, Protocol, Sequence

from . import _tasks


VENV_BIN: Final = "Scripts" if sys.platform == "win32" else "bin"


class Project(Protocol):
    @property
    def doc(self) -> Mapping[str, Any]: ...

    @property
    def managed(self) -> bool:
        match self.doc:
            case {"tool": {"uv": {"managed": bool(is_managed)}}}:
                return is_managed
            case {"tool": {"uv": _}}:
                return True
        return False

    def task(self, name: str) -> _tasks.TaskType | None:
        tasks = self._tasks()
        entry = tasks.get(name)
        if entry is None:
            executable = shutil.which(name, path=self.venv_bin_path)
            if executable and not _tasks.is_unsafe_script(Path(executable)):
                return _tasks.External(name, executable)
            return None
        return _tasks.parse_task(entry)

    def iter_tasks(self) -> Iterator[tuple[str, _tasks.TaskType]]:
        for name, entry in self._tasks().items():
            task = _tasks.parse_task(entry)
            if task is not None:
                yield name, task

    def _tasks(self) -> Mapping[str, Any]:
        match self.doc:
            case {"tool": {"uv-runner": {"tasks": {**tasks}}}}:
                return cast(Mapping[str, Any], tasks)
        return {}

    def sync(self) -> None:
        subprocess.run(['uv', 'sync', '--directory', self.root, '--frozen', '--inexact'])

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
            case {"members": [*_members]}:
                members = tuple(path for mem in _members if isinstance(mem, str)
                                for path in root.glob(mem))
            case _:
                return None
        match workspace:
            case {"exclude": [*_exclude]}:
                exclude = set(path for mem in _exclude if isinstance(mem, str)
                              for path in root.glob(mem))
            case _:
                exclude = set()
        members = tuple(m for m in members if m not in exclude)
        return cls(project.name, project.root, project.doc, members)
