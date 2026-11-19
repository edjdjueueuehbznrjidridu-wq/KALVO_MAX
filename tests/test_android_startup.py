"""Static contracts for the Android startup path (Kivy device execution is separate)."""
import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TREE = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
APP = next(node for node in TREE.body if isinstance(node, ast.ClassDef) and node.name == "KALVOApp")


def method(name):
    return next(node for node in APP.body if isinstance(node, ast.FunctionDef) and node.name == name)


def test_only_dashboard_is_eagerly_created():
    build_ui = method("_build_ui")
    added = [
        node.args[0].func.id
        for node in ast.walk(build_ui)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "add_widget"
        and node.args
        and isinstance(node.args[0], ast.Call)
        and isinstance(node.args[0].func, ast.Name)
    ]
    assert added == ["DashboardScreen"]
    factories = ast.unparse(build_ui)
    for name in ("TradeScreen", "MarketScreen", "ChartScreen", "SpotScreen", "WalletScreen", "AIScreen", "MoreScreen"):
        assert name in factories


def test_audio_loader_is_outside_build_and_on_start():
    load_calls = []
    for node in ast.walk(TREE):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "load_sounds":
            load_calls.append(node)
    assert len(load_calls) == 1
    load_method = method("_load_optional_sounds")
    assert any(node is load_calls[0] for node in ast.walk(load_method))
    assert "ui_sounds_enabled" in ast.unparse(load_method)


def test_build_wrapper_catches_python_bootstrap_exceptions():
    build = method("build")
    source = ast.unparse(build)
    assert "try" in source and "except Exception" in source
    assert "_startup_failure_view" in source
    assert "_build_ui" in source


def test_navigation_creates_a_screen_before_switching_to_it():
    navigate = ast.unparse(method("navigate_to"))
    assert "_screen_factories" in navigate
    assert "add_widget" in navigate
    assert "self.sm.current = name" in navigate
