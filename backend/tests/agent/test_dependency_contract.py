import pathlib
import re

import pytest

BACKEND_DIR = pathlib.Path(__file__).resolve().parents[2]
REQUIREMENTS_PATH = BACKEND_DIR / "requirements.txt"
LANGRAPH_PIN = "langgraph==1.2.9"
FORBIDDEN_RUNTIME_REQUIREMENTS = frozenset(
    {
        "langchain",
        "langchain-agents",
        "langgraph-server",
        "langgraph-cli",
        "langsmith",
    }
)


def _requirements_lines():
    lines = []
    for raw in REQUIREMENTS_PATH.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        lines.append(line)
    return lines


def _requirement_name(line):
    return re.split(r"[<>=!~\[\s;]", line, maxsplit=1)[0].lower()


def _langgraph_pins(lines):
    return [line for line in lines if _requirement_name(line) == "langgraph"]


def test_e0_deps_red_contract():
    if not REQUIREMENTS_PATH.exists():
        pytest.fail("RED_E0_DEPS: requirements.txt missing")
    pins = _langgraph_pins(_requirements_lines())
    if pins != [LANGRAPH_PIN]:
        pytest.fail(
            f"RED_E0_DEPS: expected exactly {LANGRAPH_PIN!r} in requirements.txt, "
            f"found {pins!r}"
        )


def test_langgraph_pinned_exactly_once():
    pins = _langgraph_pins(_requirements_lines())
    assert pins == [LANGRAPH_PIN], f"expected exactly {LANGRAPH_PIN!r}, found {pins!r}"


def test_forbidden_runtime_dependencies_absent():
    names = {_requirement_name(line) for line in _requirements_lines()}
    forbidden = sorted(names & FORBIDDEN_RUNTIME_REQUIREMENTS)
    assert not forbidden, f"forbidden direct runtime requirements: {forbidden}"
