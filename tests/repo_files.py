"""Paths and the copy inventory for files read by the fast test tiers."""

from __future__ import annotations

import errno
import inspect
import os
import shlex
import sys
import tomllib
from pathlib import Path

from typing_extensions import TypeIs

_STORES = ("hub", "runner")


def repo_root() -> Path:
    """Return this test package's checkout, including when copied into mutants/."""
    return Path(__file__).resolve().parent.parent


def audited_roots() -> tuple[Path, ...]:
    """The test code the repo-read guard audits: ``tests/`` and the store-migration tests beside the shipped revisions."""
    root = repo_root()
    colocated = (root / "src" / "blizzard" / component / "store" / "migrations" / "tests" for component in _STORES)
    return (root / "tests", *colocated)


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
        or relative.parts[0] in {".venv", ".mutation-env", "mutants"}
        # The Playwright package's installed dependencies and a run's daemon directories, which hold no test input.
        or relative.parts[:2] in {("tests", "node_modules"), ("tests", ".run")}
    ):
        return
    # pyproject.toml is read to obtain this very inventory.
    if relative != Path("pyproject.toml") and relative not in (copied if copied is not None else copied_repo_files()):
        raise AssertionError(
            f"Unlisted repo-file read: {relative}; add it to [tool.mutmut].also_copy in pyproject.toml"
        )


def _argument_list(value: object) -> TypeIs[list[object] | tuple[object, ...]]:
    return isinstance(value, (list, tuple))


def _path_operand(value: object) -> TypeIs[str | bytes | os.PathLike[str] | os.PathLike[bytes]]:
    return isinstance(value, (str, bytes, os.PathLike))


def install_repo_read_guard() -> None:
    """Catch repo-file opens and subprocess launches from fast-tier tests."""
    copied = copied_repo_files()
    roots = audited_roots()
    test_pid = os.getpid()

    def audit(event: str, args: tuple[object, ...]) -> None:
        if os.getpid() != test_pid or event not in {"open", "subprocess.Popen"}:
            return
        if event == "open":
            if not isinstance(args[0], (str, bytes, Path)):
                return
            mode = args[1]
            # A direct os.open from a test resolves against cwd; a bare name opened
            # by a library may instead be relative to an unreported dir_fd.
            if not isinstance(mode, str) and not Path(os.fsdecode(args[0])).is_absolute():
                here = inspect.currentframe()
                caller_frame = here.f_back if here else None
                caller = Path(caller_frame.f_globals.get("__file__", "") if caller_frame else "").resolve()
                if "/" not in os.fsdecode(args[0]) and not any(caller.is_relative_to(audited) for audited in roots):
                    return
            if not isinstance(mode, str) and isinstance(args[2], int) and args[2] & os.O_ACCMODE != os.O_RDONLY:
                return
            if isinstance(mode, str) and not any(flag in mode for flag in ("r", "+")):
                return
        here = inspect.currentframe()
        frame = here.f_back if here else None
        while frame:
            filename = Path(frame.f_globals.get("__file__", frame.f_code.co_filename)).resolve()
            if any(filename.is_relative_to(audited) for audited in roots) and filename.name != "repo_files.py":
                if event == "open":
                    if isinstance(args[0], (str, bytes, Path)):
                        check_repo_read(args[0], copied)
                else:
                    executable, argv, cwd = args[:3]
                    working_dir = Path(os.fsdecode(cwd)).resolve() if _path_operand(cwd) else Path.cwd()
                    # Git's -C changes where its file arguments are resolved.
                    if (
                        isinstance(executable, (str, bytes))
                        and Path(os.fsdecode(executable)).name == "git"
                        and _argument_list(argv)
                        and "-C" in argv
                    ):
                        index = argv.index("-C")
                        if index + 1 < len(argv) and _path_operand(operand := argv[index + 1]):
                            working_dir = (working_dir / os.fsdecode(operand)).resolve()
                    candidates = [executable, *(argv if _argument_list(argv) else ())]
                    if (
                        isinstance(executable, (str, bytes))
                        and Path(os.fsdecode(executable)).name in {"sh", "bash", "dash"}
                        and _argument_list(argv)
                    ):
                        for index, option in enumerate(argv[1:-1], start=1):
                            if not isinstance(option, (str, bytes)):
                                continue
                            flags = os.fsdecode(option)
                            if (
                                flags.startswith("-")
                                and not flags.startswith("--")
                                and flags[1:].isalpha()
                                and "c" in flags
                            ):
                                script = argv[index + 1]
                                if isinstance(script, (str, bytes)):
                                    # Split shell operators away from adjacent file operands.
                                    tokens = shlex.shlex(os.fsdecode(script), posix=True, punctuation_chars=";&|()<>")
                                    tokens.whitespace_split = True
                                    candidates.extend(tokens)
                                break
                    for candidate in candidates:
                        if isinstance(candidate, (str, bytes, Path)):
                            path = Path(os.fsdecode(candidate))
                            target = working_dir / path
                            try:
                                is_repo_operand = (path.is_absolute() and not target.is_dir()) or target.is_file()
                            except OSError as exc:
                                # A long prompt is an argument, not a filesystem operand.
                                if exc.errno != errno.ENAMETOOLONG:
                                    raise
                                continue
                            if is_repo_operand:
                                check_repo_read(target, copied)
                return
            frame = frame.f_back

    sys.addaudithook(audit)


if __name__ == "__main__":
    prepare_mutant_tree(repo_root() / "mutants")
