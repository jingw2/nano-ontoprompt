import ast
import pathlib
import subprocess
import sys
import tomllib

import pytest

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
GUARD_PATH = BACKEND_DIR / "scripts" / "check_python_version.py"
PYPROJECT_PATH = BACKEND_DIR / "pyproject.toml"
UNSUPPORTED_PYTHON_VERSION = "UNSUPPORTED_PYTHON_VERSION"
REQUIRED_PYTHON_FLOOR = ">=3.11"


def _run_guard(*args):
    return subprocess.run(
        [sys.executable, "-c", _GUARD_RUNNER.format(guard=str(GUARD_PATH)), *args],
        capture_output=True,
        text=True,
    )


_GUARD_RUNNER = (
    "import runpy, sys;"
    "sys.argv = ['check_python_version', *sys.argv[1:]];"
    "runpy.run_path({guard!r}, run_name='__main__')"
)


def _load_guard():
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_python_version", GUARD_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_e0_py_red_contract():
    missing = []
    if not GUARD_PATH.exists():
        missing.append("scripts/check_python_version.py")
    if not PYPROJECT_PATH.exists():
        missing.append("pyproject.toml")
    if missing:
        pytest.fail("RED_E0_PY: Python floor contract missing: " + ", ".join(missing))
    metadata = tomllib.loads(PYPROJECT_PATH.read_text())
    if metadata.get("project", {}).get("requires-python") != REQUIRED_PYTHON_FLOOR:
        pytest.fail(
            f"RED_E0_PY: pyproject.toml must declare requires-python = "
            f"{REQUIRED_PYTHON_FLOOR!r}"
        )
    proc = _run_guard()
    if proc.returncode != 0:
        pytest.fail(f"RED_E0_PY: guard failed under supported Python: {proc.stderr}")


def test_require_supported_python_accepts_current_runtime():
    assert _load_guard().require_supported_python() is True


def test_require_supported_python_rejects_python_3_10_and_earlier():
    for major_minor in [(3, 10), (3, 9), (3, 8)]:
        proc = subprocess.run(
            [
                sys.executable,
                "-c",
                f"import runpy, sys;"
                f"sys.version_info = {major_minor + (0, 'final', 0)};"
                f"runpy.run_path({str(GUARD_PATH)!r}, run_name='__main__')",
            ],
            capture_output=True,
            text=True,
        )
        assert proc.returncode != 0
        message = proc.stderr + proc.stdout
        assert UNSUPPORTED_PYTHON_VERSION in message
        detected = ".".join(str(part) for part in major_minor)
        assert f"detected Python {detected}" in message
        assert "required >= 3.11" in message


def test_guard_runs_without_site_packages():
    proc = subprocess.run([sys.executable, "-S", str(GUARD_PATH)], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr


def test_guard_imports_stdlib_only():
    tree = ast.parse(GUARD_PATH.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            imported.append(node.module.split(".")[0])
    assert imported, "guard must import at least one standard-library module"
    unknown = sorted(set(imported) - set(sys.stdlib_module_names))
    assert not unknown, f"guard imports non-stdlib modules: {unknown}"


def test_pyproject_declares_python_floor():
    metadata = tomllib.loads(PYPROJECT_PATH.read_text())
    assert metadata["project"]["requires-python"] == REQUIRED_PYTHON_FLOOR
