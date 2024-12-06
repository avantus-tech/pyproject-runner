"""Tool for running tasks defined in a project's pyproject.toml file."""

from __future__ import annotations

from collections.abc import Sequence
import functools
import json
import os
from pathlib import Path
import pprint
import shutil
import sys
import textwrap
from typing import NoReturn, overload

import click

from . import _project


@click.command(
    context_settings={
        "allow_interspersed_args": False,
        "max_content_width": 120,
        "terminal_width": shutil.get_terminal_size().columns,
        "help_option_names": ["-h", "--help"],
    },
)
@click.option("--color", type=click.Choice(["auto", "always", "never"]), default=None,
              help="Control colors in output.")
@click.option("-l", "--list", "do_list", is_flag=True, default=False,
              help="List tasks from project.")
@click.option("--project", "project_path", metavar="PATH",
              type=click.Path(exists=True, dir_okay=True, resolve_path=True, path_type=Path),
              help="Use this pyproject.toml file or directory.")
@click.option("--show-project", is_flag=True, default=False,
              help="Print project information and exit.")
@click.argument("command", metavar="[COMMAND]", nargs=-1)
@click.pass_context
def main(ctx: click.Context, *, command: tuple[str, ...], color: str | None,
         do_list: bool, project_path: Path | None, show_project: bool) -> None:
    """Run a configured task or a script installed for this package."""
    match color:
        case "auto":
            ctx.color = None
        case "always":
            ctx.color = True
        case "never":
            ctx.color = False
        case _:
            if os.environ.get("NO_COLOR"):
                ctx.color = False
            elif os.environ.get("FORCE_COLOR"):
                ctx.color = True

    if project_path and project_path.is_file():
        try:
            project = _project.PyProject.load(project_path)
        except ValueError as exc:
            _error(f"{project_path}: {exc}")
    else:
        project_or_none = _project.PyProject.discover(project_path or Path().cwd())
        if project_or_none is None:
            _error("pyproject.toml not found")
        project = project_or_none

    if show_project:
        print_project(project)
    elif do_list:
        print_tasks(project)
    elif not command:
        click.echo("Provide a command to invoke with `rr <command>`.")
        click.echo("\nThe following scripts ( ) and tasks (+) are available in the environment:\n")
        print_tasks_and_scripts(project)
        click.echo(f"\nSee {click.style('`rr --help`', bold=True)} for more information")
    else:
        name, *args = command
        try:
            task = project.task(name)
            sys.exit(task.run(project, args))
        except _project.TaskLookupError as exc:
            msg = str(exc)
            if exc.__context__:
                msg = f"{msg}: {exc.__context__}"
            _error(msg)


def print_project(project: _project.PyProject) -> None:
    """Print key values read from pyproject.toml file.

    This is useful for debugging tasks.
    """
    bold = functools.partial(click.style, bold=True)
    click.echo(f"{bold('name')}  {project.name}\n"
               f"{bold('root')}  {project.root}\n"
               f"{bold('venv')}  {project.venv_path}")

    workspace = project.workspace
    if workspace:
        members = ", ".join(str(mem.relative_to(workspace.root))
                            for mem in workspace.members)
        click.echo(f"\n{bold('workspace')}\n"
                   f"  {bold('name')}     {workspace.name}\n"
                   f"  {bold('root')}     {workspace.root}\n"
                   f"  {bold('members')}  {members}")

    if project.task_names:
        width = content_width()
        click.secho("\ntasks", bold=True)
        for name in sorted(project.task_names):
            click.secho(f"  {name}", bold=True)
            try:
                task = project.task(name)
            except _project.TaskLookupError as exc:
                click.secho(f"    {exc.__context__ or exc}", fg="red")
            else:
                # Normalize task to something that looks more like JSON.
                # Basically, this turns tuples into lists, improving readability.
                entry = json.loads(json.dumps(task.to_dict()))
                for line in pprint.pformat(entry, width=width - 4, compact=True).splitlines():
                    click.echo(f"    {line}")


def content_width() -> int:
    """Get the best content width for displaying help."""
    ctx = click.get_current_context()
    width = ctx.terminal_width or 80
    if ctx.max_content_width and width > ctx.max_content_width:
        return ctx.max_content_width
    return width


def print_tasks(project: _project.PyProject) -> None:
    """Print tasks, with any associated help, to stdout."""
    style = functools.partial(click.style, bold=True)
    items = []
    for name in project.task_names:
        try:
            task = project.task(name)
        except _project.TaskLookupError:  # noqa: PERF203
            continue
        else:
            items.append((style(name), textwrap.dedent(task.help) if task.help else ""))
    print_dl(items)


def print_tasks_and_scripts(project: _project.PyProject) -> None:
    """Print tasks scripts to stdout, prefixed by a marker.

    Scripts use a space for the marker as an indication that they exist
    in the virtual environment. Tasks use a plus to indicate that they
    add additional functionality. Tasks that cannot be used because the
    definition is in error are marked with an upper-case 'E'.
    """
    tasks = [(name, False) for name in project.task_names]
    tasks += [(name, True) for name in _project.external_scripts(project.venv_bin_path)]
    tasks.sort()

    for name, is_external in tasks:
        marker = " " if is_external else click.style("+", fg="cyan")
        if not is_external:
            try:
                project.task(name)
            except _project.TaskLookupError:
                marker = click.style("E", fg="red")
        click.echo(f"{marker} {name}")


def print_dl(items: Sequence[tuple[str, str]],
             indent: int = 0, col_max: int = 20) -> None:
    """Print a definition list.

    Prints a term, followed by a definition, wrapping appropriately.
    Supports multiline formatting by preserving common indentation and
    preserving newlines, but wrapping long lines to match the given
    indentation.

    This is similar to click's HelpFormatter.write_dl() method, but
    write_dl()'s wrapping rules don't produce the desired results when
    formatting definitions that include embedded newlines.
    """
    items = [(Styled(term), Styled(definition)) for term, definition in items]
    term_width = max([1, *(len(term) for term, _ in items if len(term) <= col_max)])
    indentation = " " * (term_width + 2 + indent)
    width = content_width() - indent

    for term, definition in items:
        newline = len(term) > term_width
        click.echo(f'{"":{indent}}{term}', nl=newline or not definition)
        if definition:
            definition = "\n".join(
                textwrap.fill(line, width=width, drop_whitespace=False,
                              initial_indent=indentation, subsequent_indent=indentation)
                for line in definition.splitlines())
            if not newline:
                definition = definition[len(term) + indent:]
            click.echo(definition)


class Styled(str):
    """Wrap a potentially styled string to calculate its printable length.

    Ignores zero-width color sequences when computing the length, which,
    if not accounted for, would throw off string formatting.
    """

    __slots__ = ("_unstyled_length",)

    _unstyled_length: int

    def __len__(self) -> int:
        """Return the length of the unstyled string."""
        try:
            return self._unstyled_length
        except AttributeError:
            self._unstyled_length = len(click.unstyle(self))
        return self._unstyled_length


@overload
def _error(msg: str, exitcode: int = ...) -> NoReturn: ...
@overload
def _error(msg: str, exitcode: None) -> None: ...
def _error(msg: str, exitcode: int | None = 1) -> None:
    """Print an error message, and optionally exit."""
    prefix = click.style("error", fg="red", bold=True)
    click.echo(f"{prefix}: {msg}", err=True)
    if exitcode is not None:
        sys.exit(exitcode)


if __name__ == "__main__":
    main()
