"""Unit-tier coverage of scripts/mutation.py's scope table, test selection, config
override, tree lifecycle, and report building (blizzard:mutation)."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.unit

_SCRIPTS = Path(__file__).resolve().parent.parent / "scripts"


def _load_module(name: str, path: Path) -> ModuleType:
    """`scripts/` is not an importable package; loading by file path under a private name
    (instead of a real `import mutation` after a sys.path mutation) keeps this resolvable
    for pyright, which never sees a static import of a module it cannot locate."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


m = _load_module("mutation", _SCRIPTS / "mutation.py")

# --- scope resolution ----------------------------------------------------------------------


def test_resolve_scope_returns_the_named_scope() -> None:
    assert m.resolve_scope("cli-surface").slug == "cli-surface"


def test_resolve_scope_rejects_an_unknown_slug_naming_every_valid_one() -> None:
    with pytest.raises(m.UnknownScopeError) as excinfo:
        m.resolve_scope("nope")
    assert excinfo.value.valid_slugs == sorted(m.SCOPES)
    for slug in m.SCOPES:
        assert slug in str(excinfo.value)


def test_main_on_an_unknown_scope_exits_non_zero_without_touching_the_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    exit_code = m.main(["nope"])
    assert exit_code == 1
    assert not (tmp_path / "mutants").exists()
    err = capsys.readouterr().err
    for slug in m.SCOPES:
        assert slug in err


# --- scope table: disjointness + coverage against the real tree ----------------------------


def test_validate_scopes_holds_against_the_real_tree() -> None:
    m.validate_scopes()


def test_files_for_scope_excludes_migrations_even_when_only_mutate_would_match() -> None:
    scope = m.Scope(slug="fixture", only_mutate=("src/blizzard/hub/*",))
    paths = {
        Path("src/blizzard/hub/domain.py"),
        Path("src/blizzard/hub/store/migrations/versions/0001_init.py"),
    }
    assert m.files_for_scope(scope, paths) == {Path("src/blizzard/hub/domain.py")}


def test_validate_scopes_raises_when_a_file_is_claimed_by_two_scopes(tmp_path: Path) -> None:
    (tmp_path / "src" / "blizzard" / "hub").mkdir(parents=True)
    (tmp_path / "src" / "blizzard" / "hub" / "domain.py").write_text("")
    overlapping = {
        "a": m.Scope(slug="a", only_mutate=("src/blizzard/hub/*",)),
        "b": m.Scope(slug="b", only_mutate=("src/blizzard/hub/*",)),
    }
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(m, "SCOPES", overlapping)
        with pytest.raises(AssertionError, match="claimed by both"):
            m.validate_scopes(tmp_path)


def test_validate_scopes_raises_when_a_file_is_covered_by_no_scope(tmp_path: Path) -> None:
    (tmp_path / "src" / "blizzard" / "wire").mkdir(parents=True)
    (tmp_path / "src" / "blizzard" / "wire" / "envelope.py").write_text("")
    only_hub = {"hub-daemon": m.SCOPES["hub-daemon"]}
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(m, "SCOPES", only_hub)
        with pytest.raises(AssertionError, match="No scope covers"):
            m.validate_scopes(tmp_path)


# --- import-derived test selection ----------------------------------------------------------


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


def test_module_selected_true_when_the_test_imports_the_package_directly(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    _write(tests_root / "test_direct.py", "from blizzard.hub import domain\n")
    assert m.test_module_selected(tests_root / "test_direct.py", ("blizzard.hub",), tests_root)


def test_module_selected_false_for_an_unrelated_package(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    _write(tests_root / "test_unrelated.py", "from blizzard.wire import envelope\n")
    assert not m.test_module_selected(tests_root / "test_unrelated.py", ("blizzard.hub",), tests_root)


def test_module_selected_true_through_a_tests_helper_module(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    _write(tests_root / "helper.py", "from blizzard.hub import domain\n")
    _write(tests_root / "test_indirect.py", "from tests.helper import domain\n")
    assert m.test_module_selected(tests_root / "test_indirect.py", ("blizzard.hub",), tests_root)


def test_module_selected_follows_a_relative_import_to_a_sibling_helper(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    _write(tests_root / "sub" / "helper.py", "from blizzard.hub import domain\n")
    _write(tests_root / "sub" / "test_relative.py", "from . import helper\n")
    assert m.test_module_selected(tests_root / "sub" / "test_relative.py", ("blizzard.hub",), tests_root)


def test_module_selected_ignores_a_conftest_helper(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    _write(tests_root / "conftest.py", "from blizzard.hub import domain\n")
    _write(tests_root / "test_uses_fixture.py", "from tests.conftest import domain\n")
    assert not m.test_module_selected(tests_root / "test_uses_fixture.py", ("blizzard.hub",), tests_root)


def test_module_selected_does_not_infinite_loop_on_an_import_cycle(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    _write(tests_root / "a.py", "from tests.b import x\n")
    _write(tests_root / "b.py", "from tests.a import y\n")
    assert not m.test_module_selected(tests_root / "a.py", ("blizzard.hub",), tests_root)


def test_selected_test_files_only_returns_test_modules(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    _write(tests_root / "conftest.py", "from blizzard.hub import domain\n")
    _write(tests_root / "test_hub.py", "from blizzard.hub import domain\n")
    _write(tests_root / "helper.py", "from blizzard.hub import domain\n")
    scope = m.Scope(slug="fixture", only_mutate=(), test_selection_packages=("blizzard.hub",))
    assert m.selected_test_files(scope, tests_root, tmp_path) == ["tests/test_hub.py"]


# --- scope -> config override ---------------------------------------------------------------


def test_config_override_carries_the_scope_fields(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    _write(tests_root / "test_hub.py", "from blizzard.hub import domain\n")
    scope = m.Scope(
        slug="hub-daemon",
        only_mutate=("src/blizzard/hub/*",),
        do_not_mutate=("src/blizzard/hub/cli/*",),
        test_selection_packages=("blizzard.hub",),
    )
    override = m.config_override_for_scope(scope, tests_root, tmp_path)
    assert override.only_mutate == ["src/blizzard/hub/*"]
    assert override.do_not_mutate == [m.MIGRATIONS_EXCLUSION, "src/blizzard/hub/cli/*"]
    assert override.pytest_add_cli_args_test_selection == ["tests/test_hub.py"]


def test_apply_config_override_sets_exactly_the_three_fields() -> None:
    class FakeMutmutConfig:
        def __init__(self) -> None:
            self.only_mutate = ["stale"]
            self.do_not_mutate = ["stale"]
            self.pytest_add_cli_args_test_selection = ["stale"]
            self.untouched = "unchanged"

    cfg = FakeMutmutConfig()
    override = m.ConfigOverride(
        only_mutate=["src/blizzard/hub/*"],
        do_not_mutate=[m.MIGRATIONS_EXCLUSION],
        pytest_add_cli_args_test_selection=["tests/test_hub.py"],
    )
    m.apply_config_override(cfg, override)
    assert cfg.only_mutate == ["src/blizzard/hub/*"]
    assert cfg.do_not_mutate == [m.MIGRATIONS_EXCLUSION]
    assert cfg.pytest_add_cli_args_test_selection == ["tests/test_hub.py"]
    assert cfg.untouched == "unchanged"


def test_apply_config_override_against_a_real_mutmut_config() -> None:
    """Pins the override onto mutmut's own Config shape — a bump that renames or drops
    one of these three fields fails this test loudly instead of silently no-op'ing."""
    pytest.importorskip("mutmut")
    import importlib

    mutmut_configuration = importlib.import_module("mutmut.configuration")
    Config = mutmut_configuration.Config
    ProcessIsolation = mutmut_configuration.ProcessIsolation
    ForkServerWarmup = mutmut_configuration.ForkServerWarmup

    cfg = Config(
        also_copy=[],
        only_mutate=[],
        do_not_mutate=[],
        do_not_mutate_patterns=[],
        max_stack_depth=-1,
        debug=False,
        source_paths=[],
        resolved_mutated_source_paths=[],
        pytest_add_cli_args=[],
        pytest_add_cli_args_test_selection=[],
        mutate_only_covered_lines=False,
        timeout_multiplier=15.0,
        timeout_constant=1.0,
        type_check_command=[],
        use_setproctitle=True,
        track_dependencies=True,
        dependency_tracking_depth=-1,
        cache_invalidation_files=[],
        cache_invalidation_exclude=[],
        on_dependency_change="warn",
        use_git_change_detection=True,
        process_isolation=ProcessIsolation.FORK,
        forkserver_warmup=ForkServerWarmup.COLLECT,
        max_forkserver_restarts=3,
        preload_modules_file=None,
        log_to_file=False,
        log_file_path="mutants/mutmut-debug.log",
    )
    override = m.ConfigOverride(
        only_mutate=["src/blizzard/hub/*"],
        do_not_mutate=[m.MIGRATIONS_EXCLUSION],
        pytest_add_cli_args_test_selection=["tests/test_hub.py"],
    )
    m.apply_config_override(cfg, override)
    assert cfg.only_mutate == ["src/blizzard/hub/*"]
    assert cfg.do_not_mutate == [m.MIGRATIONS_EXCLUSION]
    assert cfg.pytest_add_cli_args_test_selection == ["tests/test_hub.py"]


# --- the mutants/ tree: scope-switch clear ----------------------------------------------------


def test_ensure_mutant_tree_creates_a_fresh_tree_with_a_scope_marker(tmp_path: Path) -> None:
    scope = m.resolve_scope("cli-surface")
    mutants_dir = tmp_path / "mutants"
    m.ensure_mutant_tree_for_scope(scope, mutants_dir)
    assert (mutants_dir / m.SCOPE_MARKER_NAME).read_text().strip() == "cli-surface"


def test_ensure_mutant_tree_keeps_content_for_the_same_scope(tmp_path: Path) -> None:
    scope = m.resolve_scope("cli-surface")
    mutants_dir = tmp_path / "mutants"
    m.ensure_mutant_tree_for_scope(scope, mutants_dir)
    sentinel = mutants_dir / "sentinel.meta"
    sentinel.write_text("kept")
    m.ensure_mutant_tree_for_scope(scope, mutants_dir)
    assert sentinel.exists()


def test_ensure_mutant_tree_clears_when_the_scope_changes(tmp_path: Path) -> None:
    mutants_dir = tmp_path / "mutants"
    m.ensure_mutant_tree_for_scope(m.resolve_scope("cli-surface"), mutants_dir)
    sentinel = mutants_dir / "sentinel.meta"
    sentinel.write_text("stale")
    m.ensure_mutant_tree_for_scope(m.resolve_scope("hub-daemon"), mutants_dir)
    assert not sentinel.exists()
    assert (mutants_dir / m.SCOPE_MARKER_NAME).read_text().strip() == "hub-daemon"


# --- report building: no mutmut import required ------------------------------------------------


def _write_meta(mutants_dir: Path, source_path: str, exit_code_by_key: dict[str, int | None]) -> None:
    meta_path = mutants_dir / f"{source_path}.meta"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps(
            {
                "exit_code_by_key": exit_code_by_key,
                "hash_by_function_name": {},
                "type_check_error_by_key": {},
                "durations_by_key": dict.fromkeys(exit_code_by_key, 0.0),
                "estimated_durations_by_key": dict.fromkeys(exit_code_by_key, 0.0),
            }
        )
    )


def test_build_report_counts_every_known_status(tmp_path: Path) -> None:
    mutants_dir = tmp_path / "mutants"
    _write_meta(
        mutants_dir,
        "src/blizzard/hub/domain.py",
        {
            "blizzard.hub.domain.x_run__mutmut_1": 1,  # killed
            "blizzard.hub.domain.x_run__mutmut_2": 0,  # survived
            "blizzard.hub.domain.x_run__mutmut_3": None,  # not checked
            "blizzard.hub.domain.x_run__mutmut_4": 2,  # check was interrupted by user
        },
    )
    report = m.build_report(scope="hub-daemon", mutants_dir=mutants_dir, diff_provider=lambda name, path: "diff")
    assert report["scope"] == "hub-daemon"
    assert report["total"] == 4
    assert report["counts"]["killed"] == 1
    assert report["counts"]["survived"] == 1
    assert report["counts"]["not checked"] == 1
    assert report["counts"]["check was interrupted by user"] == 1
    assert sum(report["counts"].values()) == report["total"]


def test_build_report_survivors_carry_mutant_file_function_and_diff(tmp_path: Path) -> None:
    mutants_dir = tmp_path / "mutants"
    _write_meta(
        mutants_dir,
        "src/blizzard/hub/domain.py",
        {"blizzard.hub.domain.x_run__mutmut_2": 0},
    )
    report = m.build_report(
        scope="hub-daemon", mutants_dir=mutants_dir, diff_provider=lambda name, path: f"diff for {name}"
    )
    (survivor,) = report["survivors"]
    assert survivor["mutant"] == "blizzard.hub.domain.x_run__mutmut_2"
    assert survivor["file"] == "src/blizzard/hub/domain.py"
    assert survivor["function"] == "run"
    assert survivor["diff"] == "diff for blizzard.hub.domain.x_run__mutmut_2"


def test_build_report_reads_a_method_survivor_s_function_and_class() -> None:
    function_name, class_name = m._function_and_class_from_mutant_name("blizzard.hub.domain.xǁWidgetǁrun__mutmut_2")
    assert function_name == "run"
    assert class_name == "Widget"
