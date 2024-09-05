
import os
import sys
from typing import Any, Final


_IS_WINDOWS: Final = sys.platform == "win32"


class Environment:
    __slots__ = '__dict__',

    def __init__(self, env: dict[str, str]) -> None:
        super().__setattr__('__dict__', env)

    def __repr__(self) -> str:
        cls = super().__getattribute__('__class__')
        env = super().__getattribute__('__dict__')
        return f'{cls.__module__}.{cls.__qualname__}({env!r})'

    def clear(self) -> None:
        super().__getattribute__('__dict__').clear()

    def get(self, name: str, default: str = '') -> str:
        if _IS_WINDOWS:
            name = name.upper()
        return str(super().__getattribute__('__dict__').get(name, default))

    def setdefault(self, name: str, default: str = '') -> str:
        if _IS_WINDOWS:
            name = name.upper()
        return str(super().__getattribute__('__dict__').setdefault(name, str(default)))

    def pop(self, name: str, default: str = '') -> str:
        if _IS_WINDOWS:
            name = name.upper()
        return str(super().__getattribute__('__dict__').pop(name, default))

    def __getitem__(self, name: str) -> str:
        if _IS_WINDOWS:
            name = name.upper()
        return str(super().__getattribute__('__dict__').get(name, ''))

    def __setitem__(self, name: str, value: Any) -> None:
        if _IS_WINDOWS:
            name = name.upper()
        super().__getattribute__('__dict__')[name] = str(value)

    def __delitem__(self, name: str) -> None:
        if _IS_WINDOWS:
            name = name.upper()
        super().__getattribute__('__dict__').pop(name, None)

    def __getattr__(self, name: str) -> Any:
        if _IS_WINDOWS:
            name = name.upper()
        try:
            return super().__getattribute__(name)
        except AttributeError:
            return ''

    def __setattr__(self, name: str, value: Any) -> None:
        if _IS_WINDOWS:
            name = name.upper()
        super().__setattr__(name, str(value))

    def __delattr__(self, name: str) -> None:
        if _IS_WINDOWS:
            name = name.upper()
        try:
            super().__delattr__(name)
        except AttributeError:
            pass


def load_environment(source: str, env: dict[str, str] | None = None) -> dict[str, str]:
    env = env.copy() if env else {}
    namespace = {'env': Environment(env), 'os': os, 'sys': sys}
    exec(source, namespace)
    return env


def load_environment_file(path: str | os.PathLike[str],
                          env: dict[str, str] | None = None) -> dict[str, str]:
    with open(path, encoding='utf-8') as file:
        source = file.read()
    return load_environment(source, env)
