"""The fold tool over synthetic trees: which revisions it absorbs and deletes, what ``ABSORBED`` carries, and the
seed rule — a minted id and a stamped instant are regenerated, every other seeded value is kept."""

from __future__ import annotations

import json
import subprocess
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.migrations import MigrationRunner
from blizzard.runner.store import MIGRATIONS_DIR as RUNNER_MIGRATIONS_DIR
from blizzard.tools.store_fold.baseline import write_baseline
from blizzard.tools.store_fold.fold import fold_store, fold_tree
from blizzard.tools.store_fold.seeds import classify, normalize
from blizzard.tools.store_fold.tree import FoldError, read_tree

pytestmark = pytest.mark.unit

_TENANT = "ten_01M5CVZDW2DTC3F1E61GG0K8F4"
_PROJECT = "proj_01M5CVZF08E7RJF9XMYGV88P26"
_OTHER_TENANT = "ten_01M5CVZDW2DTC3F1E61GG0K8F5"
_AT = datetime(2026, 10, 1, tzinfo=UTC)

_RENDERED: dict[str, Any] = {
    "upgrade": (
        "    op.create_table('tenants',\n"
        "    sa.Column('tenant_id', sa.String(), nullable=False),\n"
        "    sa.Column('created_at', UtcDateTime(), nullable=False),\n"
        "    sa.PrimaryKeyConstraint('tenant_id')\n"
        "    )\n"
        "    op.create_table('projects',\n"
        "    sa.Column('project_id', sa.String(), nullable=False),\n"
        "    sa.Column('tenant_id', sa.String(), nullable=False),\n"
        "    sa.Column('name', sa.String(), nullable=False),\n"
        "    sa.PrimaryKeyConstraint('project_id')\n"
        "    )\n"
    ),
    "downgrade": "    op.drop_table('projects')\n    op.drop_table('tenants')\n",
    "tables": {
        "tenants": [{"tenant_id": _TENANT, "created_at": {"$datetime": _AT.isoformat()}}],
        "projects": [{"project_id": _PROJECT, "tenant_id": _TENANT, "name": "default"}],
    },
    "sequences": {},
}


def _revision(name: str, down: str | None, extra: str = "") -> str:
    return (
        f'revision = "{name}"\ndown_revision = {down!r}\n{extra}def upgrade(): pass\ndef downgrade(): pass\n'
    ).replace("'", '"')


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path: Path, chain: list[str], floors: dict[str, str]) -> Path:
    """A repo whose hub and runner trees both hold ``chain``, and a tag per ``floors`` entry (tag -> revision)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    for tree in ("hub", "runner"):
        migrations = repo / "src" / "blizzard" / tree / "store" / "migrations"
        (migrations / "versions").mkdir(parents=True)
        (migrations / "tests").mkdir()
        for index, name in enumerate(chain):
            (migrations / "versions" / f"{name}.py").write_text(_revision(name, chain[index - 1] if index else None))
            (migrations / "tests" / f"{name}_test.py").write_text("")
    for tag, head in floors.items():
        for tree in ("hub", "runner"):
            document = repo / "contracts" / "store" / f"{tree}.json"
            document.parent.mkdir(parents=True, exist_ok=True)
            document.write_text(json.dumps({"store": tree, "head_revision": head, "tables": {}}))
        _git(repo, "add", "-A")
        _git(repo, "commit", "-q", "-m", tag, "--allow-empty")
        _git(repo, "tag", tag)
    return repo


def _rendering(tree: str) -> dict[str, Any]:
    return {"upgrade": "", "downgrade": "", "tables": {}, "sequences": {}}


def _names(repo: Path, tree: str) -> set[str]:
    return {path.stem for path in (repo / "src" / "blizzard" / tree / "store" / "migrations" / "versions").glob("*.py")}


def test_a_fold_absorbs_everything_below_the_floor_and_keeps_what_is_above(tmp_path: Path) -> None:
    repo = _repo(tmp_path, ["a", "b", "c", "d"], {"v1.0.0": "c"})
    folds = fold_store(repo, "v1.0.0", _rendering)

    for tree, fold in zip(("hub", "runner"), folds, strict=True):
        assert _names(repo, tree) == {"c", "d"}
        tests = repo / "src" / "blizzard" / tree / "store" / "migrations" / "tests"
        assert {path.stem for path in tests.glob("*.py")} == {"d_test"}
        assert {path.stem for path in fold.deleted} == {"a", "b", "a_test", "b_test", "c_test"}
        tree_after = read_tree(repo / "src" / "blizzard" / tree / "store" / "migrations" / "versions")
        assert tree_after["c"].down_revision is None
        assert tree_after["c"].absorbed == {"a", "b"}
        assert tree_after["d"].down_revision == "c"
        assert 'revision = "d"' in tree_after["d"].path.read_text()


def test_a_second_fold_carries_the_first_baselines_absorbed_ids_and_its_own_id(tmp_path: Path) -> None:
    repo = _repo(tmp_path, ["a", "b", "c", "d"], {"v1.0.0": "c", "v1.1.0": "d"})
    fold_store(repo, "v1.0.0", _rendering)
    fold_store(repo, "v1.1.0", _rendering)

    tree_after = read_tree(repo / "src" / "blizzard" / "hub" / "store" / "migrations" / "versions")
    assert set(tree_after) == {"d"}
    assert tree_after["d"].absorbed == {"a", "b", "c"}
    assert "d" not in tree_after["d"].absorbed


def test_a_tree_whose_floor_revision_is_already_its_base_is_left_alone(tmp_path: Path) -> None:
    repo = _repo(tmp_path, ["a", "b"], {"v1.0.0": "a"})
    before = {path: path.read_text() for path in repo.rglob("*.py")}
    folds = fold_store(repo, "v1.0.0", _rendering)
    assert [fold.baseline for fold in folds] == [None, None]
    assert {path: path.read_text() for path in repo.rglob("*.py")} == before


def test_a_tag_with_no_store_document_is_refused_naming_the_tag_and_the_document(tmp_path: Path) -> None:
    repo = _repo(tmp_path, ["a", "b", "c"], {"v1.0.0": "b"})
    (repo / "contracts" / "store" / "runner.json").unlink()
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "no runner document")
    _git(repo, "tag", "v1.0.1")
    before = {path: path.read_text() for path in repo.rglob("*.py")}
    with pytest.raises(FoldError, match=r"v1\.0\.1.*contracts/store/runner\.json"):
        fold_store(repo, "v1.0.1", _rendering)
    assert {path: path.read_text() for path in repo.rglob("*.py")} == before


def test_a_floor_revision_the_tree_does_not_hold_is_refused(tmp_path: Path) -> None:
    repo = _repo(tmp_path, ["a", "b"], {"v1.0.0": "elsewhere"})
    with pytest.raises(FoldError, match="elsewhere"):
        fold_tree(repo, "v1.0.0", "hub", _rendering)


def test_classification_regenerates_only_instants_and_minted_ids() -> None:
    assert classify(_AT) == "timestamp"
    assert classify(_TENANT) == "minted"
    assert classify(f'[{{"new": "{_PROJECT}"}}]') == "minted"
    for kept in ("default", 1, None, True, "ten_short", "Xten_01M5CVZDW2DTC3F1E61GG0K8F4", b"bytes"):
        assert classify(kept) == "literal"


def test_normalization_equates_a_consistent_renaming_and_exposes_every_other_difference() -> None:
    def rows(tenant: str, project: str, at: datetime, name: str = "default") -> dict[str, list[dict[str, Any]]]:
        return {
            "tenants": [{"tenant_id": tenant, "created_at": at}],
            "projects": [{"project_id": project, "tenant_id": tenant, "name": name, "ref": f"{tenant}/{project}"}],
        }

    first = normalize(rows(_TENANT, _PROJECT, _AT))
    assert first == normalize(rows(_OTHER_TENANT, "proj_01M5CVZF08E7RJF9XMYGV88P27", _AT + timedelta(days=3)))
    assert first != normalize(rows(_TENANT, _PROJECT, _AT, name="renamed"))
    # A reference that stops pointing at the row it did is a difference, not a renaming.
    crossed = rows(_TENANT, _PROJECT, _AT)
    crossed["projects"][0]["tenant_id"] = _OTHER_TENANT
    assert first != normalize(crossed)


def _baseline_runner(tmp_path: Path, name: str, clock: FixedClock) -> tuple[MigrationRunner, str]:
    scripts = tmp_path / name / "scripts"
    versions = scripts / "versions"
    versions.mkdir(parents=True)
    (scripts / "env.py").write_text((RUNNER_MIGRATIONS_DIR / "env.py").read_text())
    source = write_baseline(
        tree="runner", revision="base_rev", floor_tag="v1.0.0", absorbed=frozenset({"old_rev"}), rendered=_RENDERED
    )
    (versions / "base.py").write_text(source)
    url = f"sqlite:///{tmp_path / name / 'store.db'}"
    return MigrationRunner(scripts, url, clock=clock), url


def test_a_baseline_builds_the_schema_and_regenerates_minted_ids_and_instants_at_upgrade(tmp_path: Path) -> None:
    clock = FixedClock(datetime(2027, 1, 2, 3, 4, 5, tzinfo=UTC))
    seen: list[dict[str, Any]] = []
    for name in ("first", "second"):
        runner, url = _baseline_runner(tmp_path, name, clock)
        assert runner.baseline_floor == "v1.0.0"
        assert runner.absorbed == {"old_rev"}
        runner.upgrade("base_rev")
        engine = sa.create_engine(url)
        with engine.connect() as connection:
            (tenant,) = connection.execute(sa.text("SELECT tenant_id, created_at FROM tenants")).all()
            (project,) = connection.execute(sa.text("SELECT project_id, tenant_id, name FROM projects")).all()
        engine.dispose()
        assert tenant.tenant_id.startswith("ten_") and tenant.tenant_id != _TENANT
        assert project.project_id.startswith("proj_") and project.project_id != _PROJECT
        assert project.tenant_id == tenant.tenant_id
        assert project.name == "default"
        assert str(tenant.created_at).startswith("2027-01-02 03:04:05")
        seen.append({"tenant": tenant.tenant_id, "project": project.project_id})
        runner.downgrade("base")
        assert sa.inspect(sa.create_engine(url)).get_table_names() == ["alembic_version"]
    assert seen[0] != seen[1]


def test_a_failure_on_the_second_tree_leaves_the_first_unfolded(tmp_path: Path) -> None:
    repo = _repo(tmp_path, ["a", "b", "c"], {"v1.0.0": "b"})
    before = {path: path.read_text() for path in repo.rglob("*.py")}

    def render(tree: str) -> dict[str, Any]:
        if tree == "runner":
            raise FoldError("the runner history failed to render")
        return _rendering(tree)

    with pytest.raises(FoldError, match="runner history"):
        fold_store(repo, "v1.0.0", render)
    assert {path: path.read_text() for path in repo.rglob("*.py")} == before
