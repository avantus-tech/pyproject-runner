from __future__ import annotations

import json
import os
from pathlib import Path
import pprint
import shutil
import textwrap
from typing import Any, Final, Mapping, NoReturn, overload, Sequence, TYPE_CHECKING

import click

from . import _project


class Styled(str):
    __slots__ = 'style',

    def __new__(cls, /, text: Any, **kwargs: Any) -> Styled:
        return super().__new__(cls, text)

    if TYPE_CHECKING:
        def __init__(
            self,
            /,
            text: Any,
            *,
            fg: int | tuple[int, int, int] | str | None = None,
            bg: int | tuple[int, int, int] | str | None = None,
            bold: bool | None = None,
            dim: bool | None = None,
            underline: bool | None = None,
            overline: bool | None = None,
            italic: bool | None = None,
            blink: bool | None = None,
            reverse: bool | None = None,
            strikethrough: bool | None = None,
            reset: bool = True,
        ) -> None:
            self.style: Final[Mapping[str, Any]] = {}
    else:
        def __init__(self, /, text: str, **kwargs: Any) -> None:
            self.style = kwargs

    def __repr__(self) -> str:
        args = [super().__repr__()]
        args += [f'{k}={v!r}' for k, v in self.style.items()
                 if v is not None and (k, v) != ('reset', True)]
        return f'{self.__class__.__name__}({", ".join(args)})'

    def __str__(self) -> str:
        return click.style(super().__str__(), **self.style)

    @overload
    @classmethod
    def use_style(cls, text: str, from_style: Styled) -> Styled: ...
    @overload
    @classmethod
    def use_style(cls, text: str, from_style: str) -> str: ...

    @classmethod
    def use_style(cls, text: str, from_style: str | Styled) -> str | Styled:
        if isinstance(from_style, Styled):
            return cls(text, **from_style.style)
        return text


@click.command(
    context_settings={
        'allow_interspersed_args': False,
        'max_content_width': 120,
        'terminal_width': shutil.get_terminal_size().columns,
        'help_option_names': ['-h', '--help']
    },
)
@click.option('--color', type=click.Choice(['auto', 'always', 'never']), default=None,
              help='Control colors in output.')
@click.option('-l', '--list', 'do_list', is_flag=True, default=False,
              help="List tasks from project.")
@click.option("--project", 'project_path', metavar="PATH",
              type=click.Path(exists=True, dir_okay=True, resolve_path=True, path_type=Path),
              help="Use this pyproject.toml file or directory.")
@click.option('--show-project', is_flag=True, default=False,
              help='Print project information and exit.')
@click.argument('command', metavar="[COMMAND]", nargs=-1)
@click.pass_context
def main(ctx: click.Context, command: tuple[str, ...], color: str | None,
         do_list: bool, project_path: Path | None, show_project: bool) -> None:
    """Runs a configured task or a script installed for this package."""
    match color:
        case 'auto':
            ctx.color = None
        case 'always':
            ctx.color = True
        case 'never':
            ctx.color = False
        case _:
            if os.environ.get('NO_COLOR'):
                ctx.color = False
            elif os.environ.get('FORCE_COLOR'):
                ctx.color = True

    if project_path and project_path.is_file():
        project: _project.PyProject | None = _project.PyProject.load(project_path)
    else:
        project = _project.PyProject.discover(project_path or Path().resolve())
    if project is None:
        _error('did not find pyproject.toml')

    if show_project:
        print_project(project)
    elif do_list:
        print_tasks(project)
    elif not command:
        print_tasks_and_scripts(project)
    else:
        name, *args = command
        try:
            task = project.task(name)
            exit(task.run(args, project))
        except _project.TaskLookupError as exc:
            msg = str(exc)
            if exc.__context__:
                msg = f'{msg}: {exc.__context__}'
            _error(msg)


def print_project(project: _project.PyProject) -> None:
    print_dl([
        (Styled('name', bold=True), project.name),
        (Styled('root', bold=True), str(project.root)),
        (Styled('venv', bold=True), str(project.venv_path)),
    ])
    workspace = project.workspace
    if workspace:
        members = ', '.join(str(mem.relative_to(workspace.root))
                            for mem in workspace.members)
        click.secho('\nworkspace', bold=True)
        print_dl((
            (Styled('name', bold=True), workspace.name),
            (Styled('root', bold=True), str(workspace.root)),
            (Styled('members', bold=True), members),
        ), indent=2)
    if project.task_names:
        ctx = click.get_current_context()
        width = ctx.terminal_width or 80
        if ctx.max_content_width and width > ctx.max_content_width:
            width = ctx.max_content_width
        click.secho('\ntasks', bold=True)
        for name in sorted(project.task_names):
            click.secho(f'  {name}', bold=True)
            try:
                task = project.task(name)
            except _project.TaskLookupError as exc:
                click.secho(f'    {exc.__context__ or exc}', fg='red')
            else:
                # Normalize task to something that looks more like JSON.
                # Basically, this turns tuples into lists.
                entry = json.loads(json.dumps(task.to_dict()))
                for line in pprint.pformat(entry, width=width - 4, compact=True).splitlines():
                    click.echo(f'    {line}')


def print_tasks(project: _project.PyProject) -> None:
    """Print tasks to stdout."""
    items = []
    for name in project.task_names:
        try:
            task = project.task(name)
        except _project.TaskLookupError:
            continue
        else:
            items.append((Styled(name, fg='cyan', bold=True), task.help or ''))
    print_dl(items)


def print_tasks_and_scripts(project: _project.PyProject) -> None:
    """Print tasks, and optionally scripts, to stdout."""
    tasks: list[tuple[str, bool]] = [(name, False) for name in project.task_names]
    tasks += [(name, True) for name in _project.external_scripts(project.venv_bin_path)]
    tasks.sort()

    for name, is_external in tasks:
        marker = ' ' if is_external else click.style('+', fg='cyan')
        if not is_external:
            try:
                project.task(name)
            except _project.TaskLookupError:
                marker = click.style('E', fg='red')
        click.echo(f'{marker} {name}')


def print_dl(items: Sequence[tuple[str, str]],
             indent: int = 0, col_max: int = 20) -> None:
    """Print a definition list.

    Prints a term, followed by a definition, performing wrapping and
    styling as appropriate.
    """
    ctx = click.get_current_context()
    width = ctx.terminal_width or 80
    if ctx.max_content_width and width > ctx.max_content_width:
        width = ctx.max_content_width
    width -= indent
    term_width = min(col_max, max(len(term) for term, _ in items
                                  if len(term) <= col_max))
    indentation = ' ' * (term_width + 2 + indent)

    for term, definition in items:
        nl = len(term) > term_width
        click.echo(f'{"":{indent}}{term}', nl=nl or not definition)
        if definition:
            text = '\n'.join(
                textwrap.fill(line, width=width, drop_whitespace=False,
                              initial_indent=indentation, subsequent_indent=indentation)
                for line in textwrap.dedent(definition).splitlines())
            if not nl:
                text = text[len(term) + indent:]
            click.echo(str(Styled.use_style(text, definition)))


@overload
def _error(msg: str, exitcode: int = ...) -> NoReturn: ...
@overload
def _error(msg: str, exitcode: None) -> None: ...

def _error(msg: str, exitcode: int | None = 1) -> NoReturn | None:
    """Print an error message, and optionally exit."""
    click.echo(f'{click.style("error", fg="red", bold=True)}: {msg}', err=True)
    if exitcode is not None:
        exit(exitcode)
    return None


if __name__ == '__main__':
    main()
