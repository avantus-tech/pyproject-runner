"""Tests for the _project module."""

from collections.abc import Sequence
import contextlib
import importlib
import os
import pathlib
import subprocess
import sys
import types
from typing import Any

import pytest

from pyproject_runner import _project


@pytest.fixture(params=[False, True])
def project_module(monkeypatch: pytest.MonkeyPatch,
                   request: pytest.FixtureRequest) -> types.ModuleType:
    """Reload the project module to test Windows/POSIX differences."""
    if request.param:
        if sys.platform == "win32":
            monkeypatch.setattr(sys, "platform", "linux")
    elif sys.platform != "win32":
        monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delitem(sys.modules, "pyproject_runner._project")
    importlib.invalidate_caches()

    return importlib.import_module("pyproject_runner._project")


def test_external_scripts(tmp_path: pathlib.Path, project_module: types.ModuleType) -> None:
    """Check that the platform-specific external scripts are detected."""
    scripts = tmp_path / "Scripts"
    path = scripts / "foo"
    path.mkdir(parents=True)
    (path / "bar.exe").touch(0o755)
    (scripts / "script.exe").touch(0o755)
    (scripts / "text.txt").touch(0o644)
    (scripts / "batch.bat").touch(0o644)
    (scripts / "activate.bat").touch(0o644)
    (scripts / "deactivate.bat").touch(0o644)

    bin = tmp_path / "bin"  # noqa: A001
    path = bin / "bar"
    path.mkdir(parents=True)
    (path / "foo").touch(0o755)
    (bin / "script").touch(0o755)
    (bin / "util.sh").touch(0o755)
    (bin / "text.txt").touch(0o644)
    (bin / "library.dylib").touch(0o755)
    (bin / "shared.so").touch(0o755)

    script_names = sorted(project_module.external_scripts(tmp_path / project_module.VENV_BIN))
    if sys.platform == "win32":
        assert script_names == ["batch", "script"]
    else:
        assert script_names == ["script", "util.sh"]


@pytest.mark.parametrize(("path", "expected_path"), [
    (None, None),
    ("", None),
    ("foo", "foo"),
    ("./foo", "./foo"),
    ("../foo", "../foo"),
    ("/foo", "/foo"),
    ("!!/foo", "!!/foo"),
    ("!./foo", "!./foo"),
    ("!foo", "!foo"),
    ("/!/foo", "/!/foo"),
    ("!/!/foo", "project/root/!/foo"),
    ("!/foo", "project/root/foo"),
])
def test_build_path(path: str | None, expected_path: str | None) -> None:
    """Test build_path() path substitution."""
    parent = "project/root"
    assert _project.build_path(path, parent) == expected_path
    assert _project.build_path(path, pathlib.Path(parent)) == expected_path


def test_pyproject_discover() -> None:
    """Test that this project is discovered properly."""
    path = pathlib.Path(__file__).parent
    project = _project.PyProject.discover(pathlib.Path(__file__).parent)
    assert project is not None
    assert project.name == "pyproject-runner"
    assert project.root == path.parent

    assert project.task_names == ["check", "coverage", "fix", "help",
                                  "lint", "preview", "test", "typecheck"]
    for name in project.task_names:
        task = project.task(name)
        assert isinstance(task, _project.Task)
        assert not task.executable
    script_names = _project.external_scripts(project.venv_bin_path)
    assert script_names
    for name in script_names:
        script = project.task(name)
        assert isinstance(script, _project.Task)
        assert script.executable

    workspace = project.workspace
    assert workspace is not None
    assert workspace.name == project.name
    assert workspace.root == project.root
    assert workspace.venv_path == project.venv_path
    assert workspace.venv_bin_path == project.venv_bin_path
    assert workspace.venv_python_bin == project.venv_python_bin


def test_pyproject_load_invalid(tmp_path: pathlib.Path) -> None:
    """Check loading of an emtpy pyproject.toml."""
    path = tmp_path / "pyproject.toml"
    path.touch()
    with pytest.raises(ValueError, match="invalid python project"):
        _project.PyProject.load(path)


@pytest.mark.parametrize(("task_name", "args", "returncodes", "expected_commands"), [
    ("check", ("arg2", "1 2 3"), {},
     [(["check-command", "arg1", "arg2", "1 2 3"],
       {"cwd": None, "executable": None, "env": {}})]),
    ("unknown-task", (), {}, _project.TaskLookupError("unknown task")),
    ("invalid-task", (), {}, _project.TaskLookupError("invalid task")),
])
def test_task_run(task_name: str, args: Sequence[str],
                  returncodes: dict[str, int],
                  expected_commands: list[tuple[list[str], dict[str, Any]]] | Exception,
                  monkeypatch: pytest.MonkeyPatch) -> None:
    """Check the parsing of some mock tasks."""
    project = _project.PyProject(
        name="test-project",
        root=pathlib.Path("/workspace/projects/test-project"),
        doc={"tool": {"pyproject-runner": {"tasks": {
            "check": "check-command arg1",
            "invalid-task": {"xcmd": "invalid-task"},
        }}}},
    )

    context: contextlib.AbstractContextManager[Any]
    if isinstance(expected_commands, Exception):
        context = pytest.raises(expected_commands.__class__, match=str(expected_commands))
    else:
        context = contextlib.nullcontext()
    with context:
        task = project.task(task_name)

        commands: list[tuple[list[str], dict[str, Any]]] = []

        def run(cmd: list[str], **kwargs: Any) -> types.SimpleNamespace:
            env = kwargs.pop("env")
            if env:
                kwargs["env"] = {k: env[k] for k in env.keys() - os.environ.keys()}
            commands.append((cmd, kwargs))
            return types.SimpleNamespace(returncode=returncodes.get(cmd[0], 0))

        monkeypatch.setattr(subprocess, "run", run)
        returncode = task.run(project, args)
        assert commands == expected_commands
        assert returncode == returncodes.get(task_name, 0)
