import json
from pathlib import Path
from typing import NoReturn, overload

import click

from . import _project


@click.command(
    context_settings={
        'allow_interspersed_args': False,
        'max_content_width': 120,
        'help_option_names': ['-h', '--help']
    },
)
@click.option('-l', '--list', 'do_list', is_flag=True, default=False,
              help="List tasks.")
@click.option("--project", 'project_path', metavar="PATH",
              type=click.Path(exists=True, dir_okay=True, resolve_path=True, path_type=Path),
              help="Use this pyproject.toml file or directory.")
@click.option('--show', is_flag=True, default=False,
              help='Show project information and exit.')
@click.argument('command', metavar="[COMMAND]", nargs=-1)
def main(command: tuple[str, ...], do_list: bool, project_path: Path | None, show: bool) -> None:
    """Runs a configured task or a script installed for this package."""
    if project_path and project_path.is_file():
        project: _project.PyProject | None = _project.PyProject.load(project_path)
    else:
        project = _project.PyProject.discover(project_path or Path().resolve())
    if project is None:
        _error('did not find pyproject.toml')
    if show:
        show_project(project)
        exit(0)
    if do_list or not command:
        list_tasks(project, True)
        exit(0)
    name, *args = command
    try:
        task = project.task(name)
        exit(task.run(args, project))
    except _project.TaskLookupError as exc:
        msg = str(exc)
        if exc.__context__:
            msg = f'{msg}: {exc.__context__}'
        _error(msg)


def show_project(project: _project.PyProject) -> None:
    click.echo('\n'.join([
        f'project: {project.name}',
        f'path: {project.root}',
        f'venv: {project.venv_path}',
    ]))
    workspace = project.workspace
    if workspace:
        members = ', '.join(str(mem.relative_to(workspace.root))
                            for mem in workspace.members)
        click.echo('\n'.join([
            f'workspace:',
            f'    name: {workspace.name}',
            f'    root: {workspace.root}',
            f'    members: {members}',
        ]))


def list_tasks(project: _project.PyProject, include_external: bool) -> None:
    tasks: list[tuple[str, bool]] = [(name, False) for name in project.task_names]
    if include_external:
        tasks += [(name, True) for name in _project.external_scripts(project.venv_bin_path)]
    tasks.sort()
    for name, external in tasks:
        if external:
            click.echo(name)
        else:
            try:
                task = project.task(name)
                definition = click.style(json.dumps(task.to_dict()), fg="yellow")
            except _project.TaskLookupError as exc:
                definition = click.style(str(exc.__context__ or exc), fg="red")
            name = click.style(name, fg="cyan", bold=True)
            click.echo(f'{name}  {definition}')


@overload
def _error(msg: str, exitcode: int = ...) -> NoReturn: ...
@overload
def _error(msg: str, exitcode: None) -> None: ...
def _error(msg: str, exitcode: int | None = 1) -> NoReturn | None:
    click.echo(f'{click.style("error", fg="red", bold=True)}: {msg}')
    if exitcode is not None:
        exit(exitcode)
    return None


if __name__ == '__main__':
    main()
