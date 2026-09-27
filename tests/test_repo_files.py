"""The fast tiers' repository-file reads must be reproducible in mutants/."""

from pathlib import Path

import pytest

from tests.repo_files import check_repo_read, copied_repo_files, repo_root

pytestmark = pytest.mark.unit


def test_root_is_the_checkout_containing_the_test_package() -> None:
    assert repo_root() / "tests" == Path(__file__).resolve().parent


def test_undeclared_read_is_rejected_until_its_path_is_copied() -> None:
    unlisted = Path(".github/workflows/gate.yml")
    assert (repo_root() / unlisted).is_file()
    with pytest.raises(AssertionError, match="Unlisted repo-file read"):
        (repo_root() / unlisted).read_text()
    check_repo_read(repo_root() / unlisted, copied_repo_files() | {unlisted})
    assert (repo_root() / "openapi/hub.openapi.json").read_text()
