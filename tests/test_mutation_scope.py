"""Unit-tier coverage of scripts/mutation.py's scope table, test selection, config
override, tree lifecycle, and report building (blizzard:mutation)."""

from __future__ import annotations

import importlib.util
import json
import sys
import time
import tomllib
from pathlib import Path
from types import ModuleType, SimpleNamespace

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
    monkeypatch.setattr(m, "MUTANTS_DIR", tmp_path / "mutants")
    exit_code = m.main(["nope"])
    assert exit_code == 1
    assert not (tmp_path / "mutants").exists()
    err = capsys.readouterr().err
    for slug in m.SCOPES:
        assert slug in err


def _stub_the_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, *, completed: bool) -> list[list[str]]:
    selections: list[list[str]] = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(m, "MUTANTS_DIR", tmp_path / "mutants")
    monkeypatch.setattr(
        m, "ensure_mutant_tree_for_scope", lambda scope, mutants_dir, fresh=False: ["tests/test_cli.py"]
    )

    def run_scope(
        scope: object, *, test_selection: list[str], budget_seconds: float | None, mutant_names: tuple[str, ...] = ()
    ) -> bool:
        selections.append(test_selection)
        return completed

    monkeypatch.setattr(m, "run_scope", run_scope)
    return selections


def test_main_hands_the_frozen_selection_to_the_run_and_writes_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selections = _stub_the_run(monkeypatch, tmp_path, completed=True)
    (tmp_path / "mutants").mkdir()
    assert m.main(["cli-surface"]) == 0
    assert selections == [["tests/test_cli.py"]]
    assert json.loads((tmp_path / "mutants" / m.REPORT_NAME).read_text())["scope"] == "cli-surface"


def test_main_exits_the_budget_code_with_an_incomplete_report_when_the_budget_expires(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_the_run(monkeypatch, tmp_path, completed=False)
    (tmp_path / "mutants").mkdir()
    assert m.main(["cli-surface", "--budget", "5"]) == m.BUDGET_EXIT_CODE
    report = json.loads((tmp_path / "mutants" / m.REPORT_NAME).read_text())
    assert report["complete"] is False


def test_main_with_fresh_asks_for_a_cleared_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _stub_the_run(monkeypatch, tmp_path, completed=True)
    asked: list[bool] = []

    def ensure(scope: object, mutants_dir: Path, fresh: bool = False) -> list[str]:
        asked.append(fresh)
        return []

    monkeypatch.setattr(m, "ensure_mutant_tree_for_scope", ensure)
    (tmp_path / "mutants").mkdir()
    assert m.main(["cli-surface", "--fresh"]) == 0
    assert m.main(["cli-surface"]) == 0
    assert asked == [True, False]


def test_main_with_an_unknown_revision_exits_non_zero_naming_it_and_runs_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    selections = _stub_the_run(monkeypatch, tmp_path, completed=True)
    assert m.main(["cli-surface", "--since", "no-such-revision-anywhere"]) == m.BAD_REVISION_EXIT_CODE
    assert "no-such-revision-anywhere" in capsys.readouterr().err
    assert selections == []
    assert not (tmp_path / "mutants").exists()


def test_main_with_no_changed_function_writes_an_empty_complete_report_without_running(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    selections = _stub_the_run(monkeypatch, tmp_path, completed=True)
    monkeypatch.setattr(m, "changed_mutant_globs", lambda scope, since: [])
    assert m.main(["cli-surface", "--since", "abc123"]) == 0
    assert selections == []
    report = json.loads((tmp_path / "mutants" / m.REPORT_NAME).read_text())
    assert (report["since"], report["complete"], report["total"], report["survivors"]) == ("abc123", True, 0, [])


def test_main_with_since_hands_the_changed_function_globs_to_the_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _stub_the_run(monkeypatch, tmp_path, completed=True)
    (tmp_path / "mutants").mkdir()
    monkeypatch.setattr(m, "changed_mutant_globs", lambda scope, since: ["blizzard.cli.x.x_run__mutmut_*"])
    handed: list[tuple[str, ...]] = []

    def run_scope(
        scope: object, *, test_selection: list[str], budget_seconds: float | None, mutant_names: tuple[str, ...]
    ) -> bool:
        handed.append(mutant_names)
        return True

    monkeypatch.setattr(m, "run_scope", run_scope)
    assert m.main(["cli-surface", "--since", "abc123"]) == 0
    assert handed == [("blizzard.cli.x.x_run__mutmut_*",)]


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


def test_module_selected_follows_a_helper_imported_from_the_tests_package(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    _write(tests_root / "helper.py", "from blizzard.hub import domain\n")
    _write(tests_root / "test_package_import.py", "from tests import helper\n")
    assert m.test_module_selected(tests_root / "test_package_import.py", ("blizzard.hub",), tests_root)


def test_module_selected_true_when_the_package_is_imported_from_its_parent(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    _write(tests_root / "test_parent_import.py", "from blizzard import hub\n")
    assert m.test_module_selected(tests_root / "test_parent_import.py", ("blizzard.hub",), tests_root)


_UNIT = "import pytest\n\npytestmark = pytest.mark.unit\n"


def test_selected_test_files_only_returns_test_modules(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    _write(tests_root / "conftest.py", "from blizzard.hub import domain\n" + _UNIT)
    _write(tests_root / "test_hub.py", "from blizzard.hub import domain\n" + _UNIT)
    _write(tests_root / "helper.py", "from blizzard.hub import domain\n" + _UNIT)
    scope = m.Scope(slug="fixture", only_mutate=(), test_selection_packages=("blizzard.hub",))
    assert m.selected_test_files(scope, tests_root, tmp_path) == ["tests/test_hub.py"]


def test_selected_test_files_leaves_out_a_file_with_no_unit_marked_test(tmp_path: Path) -> None:
    tests_root = tmp_path / "tests"
    _write(tests_root / "test_unit.py", "from blizzard.hub import domain\n" + _UNIT)
    _write(
        tests_root / "test_decorated.py",
        "from blizzard.hub import domain\nfrom pytest import mark\n\n@mark.unit\ndef test_x(): ...\n",
    )
    _write(
        tests_root / "test_component.py",
        "import pytest\nfrom blizzard.hub import domain\n\npytestmark = pytest.mark.component\n",
    )
    scope = m.Scope(slug="fixture", only_mutate=(), test_selection_packages=("blizzard.hub",))
    assert m.selected_test_files(scope, tests_root, tmp_path) == ["tests/test_decorated.py", "tests/test_unit.py"]


def test_the_selection_tier_is_the_one_pyproject_hands_mutmut() -> None:
    pyproject = tomllib.loads((_SCRIPTS.parent / "pyproject.toml").read_text())
    assert pyproject["tool"]["mutmut"]["pytest_add_cli_args"] == ["-m", m.MUTATION_TIER_MARKER]


def test_global_exclusions_carry_pyproject_s_do_not_mutate() -> None:
    pyproject = tomllib.loads((_SCRIPTS.parent / "pyproject.toml").read_text())
    for pattern in pyproject["tool"]["mutmut"]["do_not_mutate"]:
        assert pattern in m.GLOBAL_EXCLUSIONS


# --- scope -> config override ---------------------------------------------------------------


def test_config_override_carries_the_scope_fields() -> None:
    scope = m.Scope(
        slug="hub-daemon",
        only_mutate=("src/blizzard/hub/*",),
        do_not_mutate=("src/blizzard/hub/cli/*",),
        test_selection_packages=("blizzard.hub",),
    )
    override = m.config_override_for_scope(scope, ["tests/test_hub.py"])
    assert override.only_mutate == ["src/blizzard/hub/*"]
    assert override.do_not_mutate == [*m.GLOBAL_EXCLUSIONS, "src/blizzard/hub/cli/*"]
    assert override.pytest_add_cli_args_test_selection == ["tests/test_hub.py"]


_MIGRATIONS = "src/blizzard/*/store/migrations/versions/*"


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
        do_not_mutate=[_MIGRATIONS],
        pytest_add_cli_args_test_selection=["tests/test_hub.py"],
    )
    m.apply_config_override(cfg, override)
    assert cfg.only_mutate == ["src/blizzard/hub/*"]
    assert cfg.do_not_mutate == [_MIGRATIONS]
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
        do_not_mutate=[_MIGRATIONS],
        pytest_add_cli_args_test_selection=["tests/test_hub.py"],
    )
    m.apply_config_override(cfg, override)
    assert cfg.only_mutate == ["src/blizzard/hub/*"]
    assert cfg.do_not_mutate == [_MIGRATIONS]
    assert cfg.pytest_add_cli_args_test_selection == ["tests/test_hub.py"]


# --- the mutants/ tree: scope-switch clear ----------------------------------------------------


def _ensure(slug: str, tmp_path: Path) -> list[str]:
    return m.ensure_mutant_tree_for_scope(m.resolve_scope(slug), tmp_path / "mutants", tmp_path / "tests", tmp_path)


def test_ensure_mutant_tree_creates_a_fresh_tree_with_a_scope_marker(tmp_path: Path) -> None:
    _write(tmp_path / "tests" / "test_cli.py", "from blizzard.cli import main\n" + _UNIT)
    assert _ensure("cli-surface", tmp_path) == ["tests/test_cli.py"]
    assert (tmp_path / "mutants" / m.SCOPE_MARKER_NAME).read_text().strip() == "cli-surface"


def test_ensure_mutant_tree_keeps_content_for_the_same_scope(tmp_path: Path) -> None:
    _ensure("cli-surface", tmp_path)
    sentinel = tmp_path / "mutants" / "sentinel.meta"
    sentinel.write_text("{}")
    _ensure("cli-surface", tmp_path)
    assert sentinel.exists()


def test_ensure_mutant_tree_clears_when_the_scope_changes(tmp_path: Path) -> None:
    _ensure("cli-surface", tmp_path)
    sentinel = tmp_path / "mutants" / "sentinel.meta"
    sentinel.write_text("stale")
    _ensure("hub-daemon", tmp_path)
    assert not sentinel.exists()
    assert (tmp_path / "mutants" / m.SCOPE_MARKER_NAME).read_text().strip() == "hub-daemon"


def test_ensure_mutant_tree_clears_a_tree_with_no_scope_marker(tmp_path: Path) -> None:
    foreign = tmp_path / "mutants" / "src" / "blizzard" / "hub" / "domain.py.meta"
    foreign.parent.mkdir(parents=True)
    foreign.write_text("{}")
    _ensure("cli-surface", tmp_path)
    assert not foreign.exists()


def test_ensure_mutant_tree_reuses_the_frozen_selection_on_resume(tmp_path: Path) -> None:
    _write(tmp_path / "tests" / "test_cli.py", "from blizzard.cli import main\n" + _UNIT)
    _ensure("cli-surface", tmp_path)
    _write(tmp_path / "tests" / "test_cli_new.py", "from blizzard.cli import main\n" + _UNIT)
    assert _ensure("cli-surface", tmp_path) == ["tests/test_cli.py"]


def test_ensure_mutant_tree_reselects_on_a_scope_switch(tmp_path: Path) -> None:
    _write(tmp_path / "tests" / "test_cli.py", "from blizzard.cli import main\n" + _UNIT)
    _write(tmp_path / "tests" / "test_hub.py", "from blizzard.hub import domain\n" + _UNIT)
    _ensure("cli-surface", tmp_path)
    assert _ensure("hub-daemon", tmp_path) == ["tests/test_hub.py"]


def test_ensure_mutant_tree_drops_a_frozen_test_file_since_deleted(tmp_path: Path) -> None:
    _write(tmp_path / "tests" / "test_cli.py", "from blizzard.cli import main\n" + _UNIT)
    _write(tmp_path / "tests" / "test_cli_gone.py", "from blizzard.cli import main\n" + _UNIT)
    _ensure("cli-surface", tmp_path)
    (tmp_path / "tests" / "test_cli_gone.py").unlink()
    assert _ensure("cli-surface", tmp_path) == ["tests/test_cli.py"]


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
    assert survivor["class"] is None
    assert survivor["diff"] == "diff for blizzard.hub.domain.x_run__mutmut_2"


def test_build_report_names_a_method_survivor_s_function_and_class(tmp_path: Path) -> None:
    mutants_dir = tmp_path / "mutants"
    _write_meta(mutants_dir, "src/blizzard/hub/domain.py", {"blizzard.hub.domain.xǁWidgetǁrun__mutmut_2": 0})
    report = m.build_report(scope="hub-daemon", mutants_dir=mutants_dir, diff_provider=lambda name, path: "diff")
    (survivor,) = report["survivors"]
    assert survivor["function"] == "run"
    assert survivor["class"] == "Widget"


# --- the budget: bounds only the mutation loop -------------------------------------------------


class _FakeRunner:
    def startup(self) -> None:
        pass


def _fake_mutmut(run: object) -> SimpleNamespace:
    return SimpleNamespace(get_mutant_runner=lambda max_children=1: _FakeRunner(), _run=run)


def _mutation_loop(fake: SimpleNamespace) -> None:
    """Start the workers, then spin until SIGINT arrives — as mutmut's loop catches it."""
    fake.get_mutant_runner(4).startup()
    try:
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            time.sleep(0.01)
    except KeyboardInterrupt:
        return
    raise AssertionError("the budget never interrupted the mutation loop")


def test_the_budget_does_not_count_the_phases_before_the_mutation_loop() -> None:
    def run(_names: object, _max_children: object) -> None:
        time.sleep(0.3)  # generation, mapping, clean tests: longer than the whole budget
        fake.get_mutant_runner(4).startup()

    fake = _fake_mutmut(run)
    assert m._run_with_budget(0.05, fake) is True


def test_an_expired_budget_interrupts_the_mutation_loop() -> None:
    fake = _fake_mutmut(lambda _names, _max_children: _mutation_loop(fake))
    assert m._run_with_budget(0.05, fake) is False


def test_an_exit_after_the_budget_expires_is_the_budget_stop() -> None:
    def run(_names: object, _max_children: object) -> None:
        _mutation_loop(fake)
        raise SystemExit(1)  # a pytest run that swallowed the SIGINT, then mutmut's exit(1)

    fake = _fake_mutmut(run)
    assert m._run_with_budget(0.05, fake) is False


def test_an_exit_with_no_expired_budget_propagates() -> None:
    def run(_names: object, _max_children: object) -> None:
        raise SystemExit(1)

    with pytest.raises(SystemExit):
        m._run_with_budget(60, _fake_mutmut(run))


def test_the_runner_factory_is_restored_after_the_run() -> None:
    fake = _fake_mutmut(lambda _names, _max_children: None)
    original = fake.get_mutant_runner
    m._run_with_budget(60, fake)
    assert fake.get_mutant_runner is original


# --- .meta durability across an interrupted save -----------------------------------------------


def test_an_unreadable_meta_is_discarded_with_its_mutants_and_a_readable_one_kept(tmp_path: Path) -> None:
    mutants_dir = tmp_path / "mutants"
    _write_meta(mutants_dir, "src/blizzard/cli/ok.py", {"blizzard.cli.ok.x_run__mutmut_1": 1})
    truncated = mutants_dir / "src" / "blizzard" / "cli" / "cut.py.meta"
    truncated.write_text("")
    mutated_source = mutants_dir / "src" / "blizzard" / "cli" / "cut.py"
    mutated_source.write_text("# mutants\n")
    assert m.discard_unreadable_metas(mutants_dir) == [truncated]
    assert not mutated_source.exists()  # so mutmut regenerates the file's mutants
    assert (mutants_dir / "src" / "blizzard" / "cli" / "ok.py.meta").exists()


class _MutmutShapedData:
    """mutmut's save shape: truncate, write part of the payload, then an interrupt lands."""

    def __init__(self, meta_path: Path) -> None:
        self.meta_path = meta_path

    def save(self) -> None:
        with open(self.meta_path, "w") as f:
            f.write('{"exit_code_by_key": ')
            raise KeyboardInterrupt


def test_an_interrupted_atomic_save_leaves_the_previous_meta_intact(tmp_path: Path) -> None:
    meta_path = tmp_path / "cli.py.meta"
    meta_path.write_text('{"exit_code_by_key": {}}')

    class Data(_MutmutShapedData):
        pass

    m.install_atomic_meta_save(Data)
    data = Data(meta_path)
    with pytest.raises(KeyboardInterrupt):
        data.save()
    assert json.loads(meta_path.read_text()) == {"exit_code_by_key": {}}
    assert data.meta_path == meta_path


def test_a_completed_atomic_save_replaces_the_meta(tmp_path: Path) -> None:
    meta_path = tmp_path / "cli.py.meta"
    meta_path.write_text("{}")

    class Data:
        def __init__(self) -> None:
            self.meta_path = meta_path

        def save(self) -> None:
            with open(self.meta_path, "w") as f:
                json.dump({"exit_code_by_key": {"k": 1}}, f)

    m.install_atomic_meta_save(Data)
    m.install_atomic_meta_save(Data)  # idempotent: never wraps the wrapper
    Data().save()
    assert json.loads(meta_path.read_text()) == {"exit_code_by_key": {"k": 1}}
    assert list(tmp_path.iterdir()) == [meta_path]


_DIFF = """\
diff --git a/src/blizzard/cli/x.py b/src/blizzard/cli/x.py
--- a/src/blizzard/cli/x.py
+++ b/src/blizzard/cli/x.py
@@ -3,0 +4,2 @@ def a
+added
+added
@@ -20 +22 @@ def b
-old
+new
@@ -30,2 +31,0 @@ def c
-gone
-gone
diff --git a/src/blizzard/cli/gone.py b/src/blizzard/cli/gone.py
--- a/src/blizzard/cli/gone.py
+++ /dev/null
@@ -1,2 +0,0 @@
-x
-y
"""


def test_changed_line_ranges_reads_added_replaced_and_deleted_lines_and_skips_deleted_files() -> None:
    assert m.changed_line_ranges(_DIFF) == {"src/blizzard/cli/x.py": [(4, 5), (22, 22), (31, 30)]}


_SOURCE = """\
def free():
    return 1


class Widget:
    def run(self):
        return 2

    @staticmethod
    def make():
        def inner():
            return 3

        return inner


def last():
    return 4
"""


def test_functions_touching_names_the_free_function_a_line_falls_in() -> None:
    assert m.functions_touching(_SOURCE, [(2, 2)]) == [(None, "free")]


def test_functions_touching_names_the_method_and_its_class() -> None:
    assert m.functions_touching(_SOURCE, [(7, 7)]) == [("Widget", "run")]


def test_functions_touching_attributes_a_nested_function_to_its_enclosing_one() -> None:
    assert m.functions_touching(_SOURCE, [(12, 12)]) == [("Widget", "make")]


def test_functions_touching_counts_a_decorator_line_as_its_function() -> None:
    assert m.functions_touching(_SOURCE, [(9, 9)]) == [("Widget", "make")]


def test_functions_touching_ignores_lines_outside_every_function() -> None:
    assert m.functions_touching(_SOURCE, [(3, 5)]) == []


def test_functions_touching_places_a_pure_deletion_inside_the_function_it_shrank() -> None:
    assert m.functions_touching(_SOURCE, [(6, 5)]) == [("Widget", "run")]
    assert m.functions_touching(_SOURCE, [(2, 1)]) == [(None, "free")]
    assert m.functions_touching(_SOURCE, [(3, 2)]) == []


def test_mutant_name_globs_follow_mutmut_s_naming() -> None:
    assert m.mutant_name_globs("src/blizzard/hub/domain.py", [(None, "run"), ("Widget", "go")]) == [
        "blizzard.hub.domain.x_run__mutmut_*",
        "blizzard.hub.domain.xǁWidgetǁgo__mutmut_*",
    ]


def test_a_package_init_file_names_its_mutants_after_the_package() -> None:
    assert m.mutant_module_name("src/blizzard/__init__.py") == "blizzard"
    assert m.mutant_name_globs("src/blizzard/wire/__init__.py", [(None, "f")]) == ["blizzard.wire.x_f__mutmut_*"]


def _git_repo(root: Path) -> None:
    import subprocess

    def git(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    git("init", "-q")
    git("config", "user.email", "t@example.com")
    git("config", "user.name", "t")
    package = root / "src" / "blizzard" / "cli"
    package.mkdir(parents=True)
    (package / "x.py").write_text("def run():\n    return 1\n\n\ndef keep():\n    return 2\n")
    other = root / "src" / "blizzard" / "hub"
    other.mkdir(parents=True)
    (other / "h.py").write_text("def hub_fn():\n    return 1\n")
    git("add", "-A")
    git("commit", "-qm", "base")
    (package / "x.py").write_text("def run():\n    return 10\n\n\ndef keep():\n    return 2\n")
    (other / "h.py").write_text("def hub_fn():\n    return 10\n")
    git("commit", "-qam", "change")


def test_changed_mutant_globs_lists_only_changed_functions_inside_the_scope(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    scope = m.SCOPES["cli-surface"]
    assert m.changed_mutant_globs(scope, "HEAD~1", tmp_path) == ["blizzard.cli.x.x_run__mutmut_*"]
    assert m.changed_mutant_globs(scope, "HEAD", tmp_path) == []


def test_changed_mutant_globs_rejects_a_revision_that_names_no_commit(tmp_path: Path) -> None:
    _git_repo(tmp_path)
    with pytest.raises(m.UnknownRevisionError, match="nope"):
        m.changed_mutant_globs(m.SCOPES["cli-surface"], "nope", tmp_path)


def test_build_report_restricted_to_globs_leaves_out_every_other_mutant(tmp_path: Path) -> None:
    mutants_dir = tmp_path / "mutants"
    _write_meta(
        mutants_dir,
        "src/blizzard/hub/domain.py",
        {"blizzard.hub.domain.x_run__mutmut_1": 0, "blizzard.hub.domain.x_other__mutmut_1": 0},
    )
    report = m.build_report(
        scope="hub-daemon",
        mutants_dir=mutants_dir,
        diff_provider=lambda name, path: "diff",
        name_globs=["blizzard.hub.domain.x_run__mutmut_*"],
        since="abc",
    )
    assert report["total"] == 1
    assert [s["function"] for s in report["survivors"]] == ["run"]
    assert (report["since"], report["complete"]) == ("abc", True)


def test_a_delta_matching_no_mutant_is_a_completed_run() -> None:
    def run(_names: object, _max_children: object) -> None:
        raise AssertionError(f"{m.NO_MATCHING_MUTANTS_MESSAGE}\n\nFilter: ('x',)")

    assert m._run_with_budget(None, _fake_mutmut(run), ("x",)) is True


def test_an_unrelated_assertion_in_a_delta_run_propagates() -> None:
    def run(_names: object, _max_children: object) -> None:
        raise AssertionError("something else")

    with pytest.raises(AssertionError):
        m._run_with_budget(None, _fake_mutmut(run), ("x",))


def test_the_mutant_names_reach_mutmut_s_run() -> None:
    seen: list[object] = []
    m._run_with_budget(None, _fake_mutmut(lambda names, _max: seen.append(names)), ("a*", "b*"))
    assert seen == [("a*", "b*")]


def test_ensure_mutant_tree_with_fresh_clears_a_tree_of_the_same_scope(tmp_path: Path) -> None:
    mutants_dir = tmp_path / "mutants"
    m.ensure_mutant_tree_for_scope(m.SCOPES["cli-surface"], mutants_dir)
    stale = mutants_dir / "stale.txt"
    stale.write_text("x")
    m.ensure_mutant_tree_for_scope(m.SCOPES["cli-surface"], mutants_dir)
    assert stale.exists()
    m.ensure_mutant_tree_for_scope(m.SCOPES["cli-surface"], mutants_dir, fresh=True)
    assert not stale.exists()


# --- delta mode: every touched scope, one wall budget, one aggregate report ---------------------


def _delta_repo(root: Path) -> None:
    """`_git_repo`'s two changed functions (cli-surface, hub-daemon) plus a changed file no scope owns."""
    import subprocess

    _git_repo(root)
    tools = root / "src" / "blizzard" / "tools"
    tools.mkdir(parents=True)
    (tools / "t.py").write_text("def tool():\n    return 1\n")
    subprocess.run(["git", "add", "-A"], cwd=root, check=True)
    subprocess.run(["git", "commit", "-qm", "tool"], cwd=root, check=True)
    (tools / "t.py").write_text("def tool():\n    return 2\n")
    subprocess.run(["git", "commit", "-qam", "tool change"], cwd=root, check=True)


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _scripted_runner(clock: _Clock, mutants_dir: Path, script: dict[str, tuple[int | None, float, dict | None]]):
    calls: list[tuple[str, float]] = []

    def runner(slug: str, since: str, timeout: float):
        calls.append((slug, timeout))
        exit_code, took, report = script[slug]
        clock.now += took
        if report is not None:
            (mutants_dir / m.REPORT_NAME).write_text(json.dumps(report))
        return m.ScopeRun(exit_code, "boom" if exit_code else "")

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def _scope_report(*survivors: str) -> dict:
    return {"total": 3, "survivors": [{"mutant": name} for name in survivors]}


def test_delta_routes_changed_files_to_every_scope_they_touch(tmp_path: Path) -> None:
    _delta_repo(tmp_path)
    clock, mutants_dir = _Clock(), tmp_path / "mutants"
    runner = _scripted_runner(
        clock, mutants_dir, {"cli-surface": (0, 10, _scope_report("a")), "hub-daemon": (0, 20, _scope_report())}
    )
    report = m.run_delta("HEAD~3", repo_root=tmp_path, mutants_dir=mutants_dir, scope_runner=runner, clock=clock)
    by_scope = {entry["scope"]: entry for entry in report["scopes"]}
    assert set(by_scope) == {"cli-surface", "hub-daemon"}
    assert by_scope["cli-surface"]["status"] == "complete"
    assert by_scope["cli-surface"]["survivors"] == [{"mutant": "a"}]
    assert by_scope["cli-surface"]["elapsed_seconds"] == 10
    assert by_scope["hub-daemon"]["survivors"] == []
    assert report["elapsed_seconds"] == 30


def test_delta_reports_changed_files_no_scope_owns(tmp_path: Path) -> None:
    _delta_repo(tmp_path)
    clock, mutants_dir = _Clock(), tmp_path / "mutants"
    runner = _scripted_runner(
        clock, mutants_dir, {"cli-surface": (0, 1, _scope_report()), "hub-daemon": (0, 1, _scope_report())}
    )
    report = m.run_delta("HEAD~3", repo_root=tmp_path, mutants_dir=mutants_dir, scope_runner=runner, clock=clock)
    assert report["unscoped_files"] == ["src/blizzard/tools/t.py"]


def test_delta_with_no_changed_function_in_a_touched_scope_is_no_changes_without_a_run(tmp_path: Path) -> None:
    _delta_repo(tmp_path)
    (tmp_path / "src" / "blizzard" / "hub" / "data.json").write_text("{}")
    import subprocess

    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    subprocess.run(["git", "commit", "-qm", "data"], cwd=tmp_path, check=True)
    clock, mutants_dir = _Clock(), tmp_path / "mutants"
    runner = _scripted_runner(clock, mutants_dir, {})
    report = m.run_delta("HEAD~1", repo_root=tmp_path, mutants_dir=mutants_dir, scope_runner=runner, clock=clock)
    assert [(e["scope"], e["status"]) for e in report["scopes"]] == [("hub-daemon", "no-changes")]
    assert report["scopes"][0]["reason"]
    assert runner.calls == []  # type: ignore[attr-defined]


def test_delta_reports_an_over_budget_scope_and_skips_the_rest_keeping_finished_results(tmp_path: Path) -> None:
    _delta_repo(tmp_path)
    clock, mutants_dir = _Clock(), tmp_path / "mutants"
    runner = _scripted_runner(
        clock, mutants_dir, {"hub-daemon": (0, 40, _scope_report("a")), "cli-surface": (None, 60, None)}
    )
    report = m.run_delta(
        "HEAD~3", budget_seconds=100, repo_root=tmp_path, mutants_dir=mutants_dir, scope_runner=runner, clock=clock
    )
    by_scope = {entry["scope"]: entry for entry in report["scopes"]}
    assert by_scope["hub-daemon"]["status"] == "complete"
    assert by_scope["cli-surface"]["status"] == "over-budget"
    assert runner.calls == [("hub-daemon", 100), ("cli-surface", 60)]  # type: ignore[attr-defined]


def test_delta_budget_spent_in_preparation_leaves_later_scopes_unstarted(tmp_path: Path) -> None:
    _delta_repo(tmp_path)
    clock, mutants_dir = _Clock(), tmp_path / "mutants"
    runner = _scripted_runner(clock, mutants_dir, {"hub-daemon": (None, 100, None)})
    report = m.run_delta(
        "HEAD~3", budget_seconds=100, repo_root=tmp_path, mutants_dir=mutants_dir, scope_runner=runner, clock=clock
    )
    assert [e["status"] for e in report["scopes"]] == ["over-budget", "over-budget"]
    assert [call[0] for call in runner.calls] == ["hub-daemon"]  # type: ignore[attr-defined]


def test_delta_records_a_mutmut_failure_as_failed_with_its_reason(tmp_path: Path) -> None:
    _delta_repo(tmp_path)
    clock, mutants_dir = _Clock(), tmp_path / "mutants"
    runner = _scripted_runner(clock, mutants_dir, {"cli-surface": (1, 5, None), "hub-daemon": (0, 5, _scope_report())})
    report = m.run_delta("HEAD~3", repo_root=tmp_path, mutants_dir=mutants_dir, scope_runner=runner, clock=clock)
    by_scope = {entry["scope"]: entry for entry in report["scopes"]}
    assert by_scope["cli-surface"]["status"] == "failed"
    assert "exited 1" in by_scope["cli-surface"]["reason"] and "boom" in by_scope["cli-surface"]["reason"]
    assert by_scope["hub-daemon"]["status"] == "complete"


def test_delta_does_not_read_a_stale_scope_report_after_a_silent_child(tmp_path: Path) -> None:
    _delta_repo(tmp_path)
    clock, mutants_dir = _Clock(), tmp_path / "mutants"
    mutants_dir.mkdir()
    (mutants_dir / m.REPORT_NAME).write_text(json.dumps(_scope_report("stale")))
    runner = _scripted_runner(clock, mutants_dir, {"cli-surface": (0, 1, None), "hub-daemon": (0, 1, None)})
    report = m.run_delta("HEAD~3", repo_root=tmp_path, mutants_dir=mutants_dir, scope_runner=runner, clock=clock)
    assert {e["status"] for e in report["scopes"]} == {"failed"}


def test_delta_rejects_a_revision_that_names_no_commit_before_running(tmp_path: Path) -> None:
    _delta_repo(tmp_path)
    runner = _scripted_runner(_Clock(), tmp_path, {})
    with pytest.raises(m.UnknownRevisionError):
        m.run_delta("nope", repo_root=tmp_path, mutants_dir=tmp_path / "mutants", scope_runner=runner)
    assert runner.calls == []  # type: ignore[attr-defined]


def test_main_delta_exits_zero_whenever_the_report_is_written(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    written: list[str] = []

    def fake_run_delta(since: str, *, budget_seconds: float) -> dict:
        written.append(f"{since}:{budget_seconds:g}")
        return {"scopes": [{"status": "failed"}, {"status": "over-budget"}]}

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(m, "run_delta", fake_run_delta)
    assert m.main(["--since", "abc"]) == 0
    assert m.main(["--since", "abc", "--delta-budget", "5"]) == 0
    assert written == [f"abc:{m.DEFAULT_DELTA_BUDGET_SECONDS:g}", "abc:5"]


def test_main_without_a_scope_or_since_exits_non_zero(capsys: pytest.CaptureFixture[str]) -> None:
    assert m.main([]) == 1
    assert "--since" in capsys.readouterr().err


def test_main_delta_on_an_unresolvable_revision_exits_the_bad_revision_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def raising(since: str, *, budget_seconds: float) -> dict:
        raise m.UnknownRevisionError(since)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(m, "run_delta", raising)
    assert m.main(["--since", "nope"]) == m.BAD_REVISION_EXIT_CODE
