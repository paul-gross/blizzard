"""The fast tiers' repository-file reads must be reproducible in mutants/."""

import os
import subprocess
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


def test_subprocess_rejects_an_unlisted_repo_executable() -> None:
    unlisted = repo_root() / "src/blizzard/static/README.md"
    with pytest.raises(AssertionError, match="Unlisted repo-file read"):
        subprocess.run([str(unlisted)], check=False)
    result = subprocess.run([str(repo_root() / "scripts/image-tags.sh"), "v1.2.3"], capture_output=True, text=True)
    assert result.returncode == 0


def test_python_script_outside_source_and_tests_needs_the_copy_inventory() -> None:
    script = Path("scripts/prose_spans.py")
    with pytest.raises(AssertionError, match="Unlisted repo-file read"):
        check_repo_read(repo_root() / script, copied_repo_files() - {script})
    assert (repo_root() / script).read_text()


def test_relative_os_open_checks_the_copy_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(repo_root())
    with pytest.raises(AssertionError, match="Unlisted repo-file read"):
        os.open("src/blizzard/static/README.md", os.O_RDONLY)
    fd = os.open("openapi/hub.openapi.json", os.O_RDONLY)
    os.close(fd)


def test_shell_command_checks_repo_file_arguments(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(repo_root())
    with pytest.raises(AssertionError, match="Unlisted repo-file read"):
        subprocess.run("cat src/blizzard/static/README.md", shell=True, check=False)
    result = subprocess.run("cat openapi/hub.openapi.json", shell=True, check=False, capture_output=True)
    assert result.returncode == 0


def test_subprocess_checks_bare_repo_file_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(repo_root())
    # mutmut itself copies uv.lock, even though tests have not declared it.
    for command in (["cat", "uv.lock"], "cat uv.lock"):
        with pytest.raises(AssertionError, match="Unlisted repo-file read"):
            subprocess.run(command, shell=isinstance(command, str), check=False)
    if (repo_root() / "SECURITY.md").exists():
        with pytest.raises(AssertionError, match="Unlisted repo-file read"):
            subprocess.run(["cat", "SECURITY.md"], check=False)
    result = subprocess.run(["cat", "README.md"], check=False, capture_output=True)
    assert result.returncode == 0


def test_long_non_path_argument_does_not_prevent_process_start() -> None:
    result = subprocess.run(["/bin/true", "x" * 300], cwd=repo_root(), capture_output=True)
    assert result.returncode == 0
    with pytest.raises(AssertionError, match="Unlisted repo-file read"):
        subprocess.run(["cat", "x" * 300, "uv.lock"], cwd=repo_root(), capture_output=True)


def test_combined_shell_flags_check_repo_file_arguments() -> None:
    with pytest.raises(AssertionError, match="Unlisted repo-file read"):
        subprocess.run(["bash", "-lc", "cat uv.lock"], cwd=repo_root(), capture_output=True)
    result = subprocess.run(["bash", "-lc", "cat README.md"], cwd=repo_root(), capture_output=True)
    assert result.returncode == 0


@pytest.mark.parametrize("script", ["cat uv.lock;", "cat README.md && cat uv.lock", "cat uv.lock|wc -c"])
def test_shell_operators_do_not_hide_unlisted_file_operands(script: str) -> None:
    with pytest.raises(AssertionError, match=r"Unlisted repo-file read: uv\.lock"):
        subprocess.run(["bash", "-lc", script], cwd=repo_root(), capture_output=True)
    result = subprocess.run(["bash", "-lc", "cat README.md;"], cwd=repo_root(), capture_output=True)
    assert result.returncode == 0
