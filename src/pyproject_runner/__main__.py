from pathlib import Path
from typing import NoReturn, overload

import click

from . import _project
from . import _tasks


@click.command(
    context_settings={
        'allow_interspersed_args': False,
        'max_content_width': 120,
        'help_option_names': ['-h', '--help']
    },
)
@click.option('-l', '--list', 'do_list', is_flag=True, default=False,
              help="List tasks.")
@click.option("--pyproject", metavar="PATH",
              type=click.Path(exists=True, dir_okay=True, resolve_path=True, path_type=Path),
              help="Use this pyproject.toml file or directory.")
@click.option('--show', is_flag=True, default=False,
              help='Show project information and exit.')
@click.argument('command', metavar="[COMMAND]", nargs=-1)
def main(command: tuple[str, ...], do_list: bool, pyproject: Path | None, show: bool) -> None:
    """Runs a configured task or a script installed for this package."""
    if pyproject and pyproject.is_file():
        project: _project.PyProject | None = _project.PyProject.load(pyproject)
    else:
        project = _project.PyProject.discover(pyproject or Path().resolve())
    if project is None:
        _error('did not find pyproject.toml')
    if show:
        show_project(project)
        exit(0)
    if do_list or not command:
        list_tasks(project, True)
        exit(0)
    if project.managed:
        project.sync()
    name, *args = command
    try:
        exit(_tasks.run_task(project, name, args))
    except _tasks.RunError as exc:
        _error(str(exc))


def show_project(project: _project.PyProject) -> None:
    click.echo('\n'.join([
        f'project: {project.name}',
        f'path: {project.root}',
        f'venv: {project.venv_path}',
        f'managed: {str(project.managed).lower()}',
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
    tasks: list[tuple[str, _tasks.TaskType | None]] = list(project.iter_tasks())
    if include_external:
        tasks += [(name, None) for name in _tasks.external_scripts(project.venv_bin_path)]
    tasks.sort(key=lambda c: (c[0], c[1] is None))
    for name, task in tasks:
        if task is None:
            click.echo(name)
        else:
            click.echo(f'{click.style(name, fg="cyan", bold=True)}  {click.style(task.to_dict(), fg="yellow")}')


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
