"""Reject unsupported Python, then replace this process with a command."""

import os
import sys

from check_python_version import require_supported_python


def main():
    require_supported_python()
    command = sys.argv[1:]
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        print("GUARDED_ENTRYPOINT_USAGE: command required", file=sys.stderr)
        raise SystemExit(2)
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
