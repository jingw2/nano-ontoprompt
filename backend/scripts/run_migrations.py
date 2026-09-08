"""Run Alembic only after enforcing the supported Python version."""

import os
import sys

from check_python_version import require_supported_python


def main():
    require_supported_python()
    command = [sys.executable, "-m", "alembic", *sys.argv[1:]]
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
