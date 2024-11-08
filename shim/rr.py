import os
import sys

def main():
    try:
        os.execvp('uv', ['uv', 'run', '--', 'python3', '-m', 'pyproject_runner', *sys.argv[1:]])
    except OSError as exc:
        print(f'error: {exc}: uv', file=sys.stderr)
    exit(1)


if __name__ == "__main__":
    main()
