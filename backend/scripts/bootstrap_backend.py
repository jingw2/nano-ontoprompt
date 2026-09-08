"""Install backend dependencies after enforcing the Python floor."""

import os
from pathlib import Path
import sys

from check_python_version import require_supported_python


def main():
    require_supported_python()
    requirements = Path(__file__).resolve().parents[1] / "requirements.txt"
    command = [sys.executable, "-m", "pip", "install", "-r", str(requirements)]
    os.execvpe(command[0], command, os.environ)


if __name__ == "__main__":
    main()
