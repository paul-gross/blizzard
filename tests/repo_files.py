"""Paths and the copy inventory for files read by the fast test tiers."""

from __future__ import annotations

import os
import sys
import tomllib
from pathlib import Path


def repo_root() -> Path:
    """Return this test package's checkout, including when copied into mutants/."""
    return Path(__file__).resolve().parent.parent


def copied_repo_files() -> frozenset[Path]:
    """Expand mutmut's native copy list into paths relative to this checkout."""
    root = repo_root()
    config = tomllib.loads((root / "pyproject.toml").read_text())
    entries = config["tool"]["mutmut"]["also_copy"]
    return frozenset(
        path.relative_to(root) for entry in entries for path in (root / entry).rglob("*") if path.is_file()
    ) | frozenset(Path(entry) for entry in entries if (root / entry).is_file())


def prepare_mutant_tree(destination: Path) -> None:
    """Create parents needed by mutmut's native also_copy before its first run."""
    root = repo_root()
    config = tomllib.loads((root / "pyproject.toml").read_text())
    for entry in config["tool"]["mutmut"]["also_copy"]:
        (destination / entry).parent.mkdir(parents=True, exist_ok=True)


def check_repo_read(path: str | bytes | Path, copied: frozenset[Path] | None = None) -> None:
    """Reject a test's undeclared non-Python read before it becomes a mutation failure."""
    root = repo_root()
    resolved = Path(os.fsdecode(path)).resolve()
    if not resolved.is_relative_to(root):
        return
    relative = resolved.relative_to(root)
    if (
        not relative.parts
        or (relative.suffix == ".py" and relative.parts[0] in {"src", "tests"})
        or relative.suffix == ".pyc"
        or "__pycache__" in relative.parts
        or relative.parts[0] in {".venv", "mutants"}
    ):
        return
    # pyproject.toml is read to obtain this very inventory.
    if relative != Path("pyproject.toml") and relative not in (copied if copied is not None else copied_repo_files()):
        raise AssertionError(
            f"Unlisted repo-file read: {relative}; add it to [tool.mutmut].also_copy in pyproject.toml"
        )


def install_repo_read_guard() -> None:
    """Catch repo-file opens and subprocess launches from fast-tier tests."""
    copied = copied_repo_files()
    tests = repo_root() / "tests"
    test_pid = os.getpid()

    def audit(event: str, args: tuple[object, ...]) -> None:
        if os.getpid() != test_pid or event not in {"open", "subprocess.Popen"}:
            return
        if event == "open":
            if not isinstance(args[0], (str, bytes, Path)):
                return
            mode = args[1]
            # os.open with a relative path can be relative to a dir_fd, not the cwd.
            if not isinstance(mode, str) and not Path(os.fsdecode(args[0])).is_absolute():
                return
            if not isinstance(mode, str) and isinstance(args[2], int) and args[2] & os.O_ACCMODE != os.O_RDONLY:
                return
            if isinstance(mode, str) and not any(flag in mode for flag in ("r", "+")):
                return
        frame = sys._getframe(1)
        while frame:
            filename = Path(frame.f_globals.get("__file__", frame.f_code.co_filename)).resolve()
            if filename.is_relative_to(tests) and filename.name != "repo_files.py":
                if event == "open":
                    if isinstance(args[0], (str, bytes, Path)):
                        check_repo_read(args[0], copied)
                else:
                    executable, argv, cwd = args[:3]
                    working_dir = (
                        Path(os.fsdecode(cwd)).resolve() if isinstance(cwd, (str, bytes, os.PathLike)) else Path.cwd()
                    )
                    candidates = [executable, *(argv if isinstance(argv, (list, tuple)) else ())]
                    for candidate in candidates:
                        if isinstance(candidate, (str, bytes, Path)):
                            path = Path(os.fsdecode(candidate))
                            target = working_dir / path
                            if path.is_absolute() or ("/" in os.fsdecode(candidate) and target.is_file()):
                                check_repo_read(target, copied)
                return
            frame = frame.f_back

    sys.addaudithook(audit)


if __name__ == "__main__":
    prepare_mutant_tree(repo_root() / "mutants")
