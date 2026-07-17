"""Static safety checks for the Platform Console app source.

The app depends on streamlit (not a package dependency), so these tests parse
the source with ast instead of importing it: no module may construct a
workspace client at import time, and none may reference --apply machinery.
"""

import ast
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent.parent / "apps" / "platform-console"
APP_FILES = sorted(APP_DIR.rglob("*.py"))

FORBIDDEN_TOP_LEVEL_CALLS = {"get_client", "WorkspaceClient"}


def _top_level_calls(tree: ast.Module) -> set[str]:
    """Names called at module scope — bodies of function/class defs excluded."""
    names = set()
    for stmt in tree.body:
        if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        for node in ast.walk(stmt):
            if isinstance(node, ast.Call):
                fn = node.func
                if isinstance(fn, ast.Name):
                    names.add(fn.id)
                elif isinstance(fn, ast.Attribute):
                    names.add(fn.attr)
    return names


def test_app_source_exists():
    assert (APP_DIR / "app.yaml").exists()
    assert (APP_DIR / "requirements.txt").exists()
    assert len(APP_FILES) >= 4


def test_no_workspace_client_constructed_at_import_time():
    for path in APP_FILES:
        calls = _top_level_calls(ast.parse(path.read_text()))
        assert not (calls & FORBIDDEN_TOP_LEVEL_CALLS), (
            f"{path.name} touches the workspace at import time"
        )


def test_app_never_references_apply():
    for path in APP_FILES:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                assert node.value != "--apply", f"{path.name} passes --apply"
