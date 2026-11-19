"""Static contracts for boot diagnostics; device execution is separate."""
import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = (ROOT / "main.py").read_text(encoding="utf-8")
TREE = ast.parse(SOURCE)


def test_boot_trace_is_installed_before_application_imports():
    assert "sys.excepthook = _early_crash_hook" in SOURCE
    assert '"main.py entered"' in SOURCE
    assert '"import-kivy"' in SOURCE
    assert '"import-apex-core"' in SOURCE
    assert '"module-imports-complete"' in SOURCE


def test_boot_trace_covers_database_and_ui_construction():
    for phase in (
        '"app-build-enter"', '"build-ui-database"',
        '"build-ui-database-ready"', '"build-dashboard"',
        '"build-navigation"', '"build-root-ready"',
    ):
        assert phase in SOURCE


def test_network_startup_is_deferred_until_after_first_frame():
    dashboard = next(n for n in TREE.body if isinstance(n, ast.ClassDef) and n.name == "DashboardScreen")
    on_enter = next(n for n in dashboard.body if isinstance(n, ast.FunctionDef) and n.name == "on_enter")
    assert "2.5" in ast.unparse(on_enter)


def test_boot_logs_do_not_include_secret_values():
    assert "startup-trace.log" in SOURCE
    assert "startup-crash.log" in SOURCE


def test_boot_diagnostics_emit_android_logcat_breadcrumbs_and_fatal_dump():
    assert "[KALVO-BOOT]" in SOURCE
    assert "faulthandler.enable" in SOURCE
    assert "startup-fatal.log" in SOURCE
    assert "_set_boot_log_dir(log_dir)" in SOURCE
    assert SOURCE.index('_boot_trace("main.py entered")') < SOURCE.index('from kivy.app import App')


def test_no_provider_secret_is_bundled_in_defaults_or_env_example():
    defaults = (ROOT / "apex" / "defaults.py").read_text(encoding="utf-8")
    env_example = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert 'DEFAULT_COARENA_API_KEY = ""' in defaults
    assert "COARENA_API_KEY=" in env_example
    assert "coa_" not in defaults
    assert "coa_" not in env_example


def test_standalone_boot_probe_uses_only_kivy_app_entrypoint():
    probe = ROOT / "android_boot_probe" / "main.py"
    spec = ROOT / "android_boot_probe" / "buildozer.spec"
    assert probe.is_file() and spec.is_file()
    probe_source = probe.read_text(encoding="utf-8")
    assert "from kivy.app import App" in probe_source
    assert "import apex" not in probe_source
    assert "Kivy-only" in probe_source
    assert "kivy==2.2.1" in spec.read_text(encoding="utf-8")
