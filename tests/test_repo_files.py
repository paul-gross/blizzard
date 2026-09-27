"""The fast tiers' repository-file reads must be reproducible in mutants/."""

from pathlib import Path

import pytest

from tests.repo_files import check_repo_read, copied_repo_files, prepare_mutant_tree, repo_root

pytestmark = pytest.mark.unit


def test_root_is_the_checkout_containing_the_test_package() -> None:
    assert repo_root() / "tests" == Path(__file__).resolve().parent


def test_undeclared_read_is_rejected_until_its_path_is_copied() -> None:
    unlisted = Path("src/blizzard/static/README.md")
    assert (repo_root() / unlisted).is_file()
    with pytest.raises(AssertionError, match="Unlisted repo-file read"):
        (repo_root() / unlisted).read_text()
    check_repo_read(repo_root() / unlisted, copied_repo_files() | {unlisted})
    assert (repo_root() / "openapi/hub.openapi.json").read_text()


def test_mutmut_copy_parents_exist_without_copying_repo_roots(tmp_path: Path) -> None:
    prepare_mutant_tree(tmp_path / "mutants")
    assert (tmp_path / "mutants/.github/workflows").is_dir()
    assert (tmp_path / "mutants/web/projects/fleet/src/lib/chunk-detail").is_dir()
    assert not (tmp_path / "mutants/web/angular.json").exists()
    assert not (tmp_path / "mutants/web/node_modules").exists()
