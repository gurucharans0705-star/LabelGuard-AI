"""Startup regressions: the package must be a real package and app.py must import cleanly."""
import importlib
import re
from pathlib import Path

import labelguard
from labelguard import version

ROOT = Path(__file__).resolve().parents[1]


def test_labelguard_is_a_regular_package_with_a_version():
    # A folder without __init__.py is a "namespace package": submodules import, but the package has no
    # attributes, which is what produced "cannot import name '__version__' from 'labelguard'".
    assert labelguard.__file__ is not None, "labelguard/__init__.py is missing or unreadable"
    assert re.fullmatch(r"\d+\.\d+\.\d+", labelguard.__version__)
    assert labelguard.__version__ == version.__version__


def test_every_package_folder_has_an_init_file():
    missing = [
        str(folder.relative_to(ROOT))
        for folder in (ROOT / "labelguard").rglob("*")
        if folder.is_dir() and folder.name != "__pycache__" and any(folder.glob("*.py"))
        and not (folder / "__init__.py").exists()
    ]
    assert missing == []


def test_version_module_has_no_imports():
    source = (ROOT / "labelguard" / "version.py").read_text(encoding="utf-8")
    assert not re.search(r"^\s*(import|from)\s", source, re.M)  # cannot create a circular import


def test_app_and_every_page_module_import_cleanly():
    for name in ("labelguard.ui.state", "labelguard.ui.dashboard", "labelguard.ui.upload", "labelguard.ui.findings",
                 "labelguard.ui.visual", "labelguard.ui.results", "labelguard.ui.rules_page",
                 "labelguard.verification", "labelguard.evaluation"):
        importlib.import_module(name)


def test_app_reads_the_version_from_the_version_module():
    assert "from labelguard.version import __version__" in (ROOT / "app.py").read_text(encoding="utf-8")
