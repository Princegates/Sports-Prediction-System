"""Every script must at least parse and import.

This exists because it didn't. A careless bulk edit left a stray indent in
`scripts/bootstrap.py`, the test suite stayed green -- no test imports the
scripts -- and the breakage was found by a deploy failing several minutes
into a CI run.

Scripts are the least-covered code in the project and the most annoying place
to find a syntax error, because the feedback loop runs through a deployment.
A compile check is cheap and catches the whole class.
"""

from __future__ import annotations

import importlib.util
import py_compile
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
SCRIPT_PATHS = sorted(SCRIPTS_DIR.glob("*.py"))


def test_scripts_directory_is_found():
    """Guards the guard: if the glob silently matched nothing, every test
    below would vacuously pass."""

    assert SCRIPT_PATHS, f"no scripts found under {SCRIPTS_DIR}"


@pytest.mark.parametrize("path", SCRIPT_PATHS, ids=lambda p: p.name)
def test_script_compiles(path: Path):
    """Catches syntax and indentation errors without executing anything."""

    py_compile.compile(str(path), doraise=True)


@pytest.mark.parametrize("path", SCRIPT_PATHS, ids=lambda p: p.name)
def test_script_imports(path: Path):
    """Catches bad imports too -- a module removed from a script's imports
    while still referenced, or a renamed helper.

    Scripts guard their work behind ``if __name__ == "__main__"``, so
    importing runs their module level only: imports and constants, no
    network and no database writes.
    """

    spec = importlib.util.spec_from_file_location(f"_script_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
