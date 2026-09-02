"""The inherited filesystem boundary OpenCode and every descendant of it run inside.

A direct Landlock syscall binding: the access bits, the ruleset structs, and the allowlist a
compatibility run computes from one argv, workdir, and environment.  It fails closed — an
unavailable or unenforceable boundary raises rather than running the child unconfined.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import tempfile
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

_LANDLOCK_CREATE_RULESET = 444
_LANDLOCK_ADD_RULE = 445
_LANDLOCK_RESTRICT_SELF = 446
_PR_SET_NO_NEW_PRIVS = 38
_LANDLOCK_CREATE_RULESET_VERSION = 1
_LANDLOCK_RULE_TYPE_PATH_BENEATH = 1
_LANDLOCK_ACCESS_FS_EXECUTE = 1 << 0
_LANDLOCK_ACCESS_FS_WRITE_FILE = 1 << 1
_LANDLOCK_ACCESS_FS_READ_FILE = 1 << 2
_LANDLOCK_ACCESS_FS_READ_DIR = 1 << 3
_LANDLOCK_ACCESS_FS_REMOVE_DIR = 1 << 4
_LANDLOCK_ACCESS_FS_REMOVE_FILE = 1 << 5
_LANDLOCK_ACCESS_FS_MAKE_CHAR = 1 << 6
_LANDLOCK_ACCESS_FS_MAKE_DIR = 1 << 7
_LANDLOCK_ACCESS_FS_MAKE_REG = 1 << 8
_LANDLOCK_ACCESS_FS_MAKE_SOCK = 1 << 9
_LANDLOCK_ACCESS_FS_MAKE_FIFO = 1 << 10
_LANDLOCK_ACCESS_FS_MAKE_BLOCK = 1 << 11
_LANDLOCK_ACCESS_FS_MAKE_SYM = 1 << 12
_LANDLOCK_ACCESS_FS_REFER = 1 << 13
_LANDLOCK_ACCESS_FS_TRUNCATE = 1 << 14
_LANDLOCK_ACCESS_FS_READ_EXECUTE = _LANDLOCK_ACCESS_FS_READ_FILE | _LANDLOCK_ACCESS_FS_EXECUTE
_LANDLOCK_ACCESS_FS_ALL = (
    _LANDLOCK_ACCESS_FS_EXECUTE
    | _LANDLOCK_ACCESS_FS_WRITE_FILE
    | _LANDLOCK_ACCESS_FS_READ_FILE
    | _LANDLOCK_ACCESS_FS_READ_DIR
    | _LANDLOCK_ACCESS_FS_REMOVE_DIR
    | _LANDLOCK_ACCESS_FS_REMOVE_FILE
    | _LANDLOCK_ACCESS_FS_MAKE_CHAR
    | _LANDLOCK_ACCESS_FS_MAKE_DIR
    | _LANDLOCK_ACCESS_FS_MAKE_REG
    | _LANDLOCK_ACCESS_FS_MAKE_SOCK
    | _LANDLOCK_ACCESS_FS_MAKE_FIFO
    | _LANDLOCK_ACCESS_FS_MAKE_BLOCK
    | _LANDLOCK_ACCESS_FS_MAKE_SYM
    | _LANDLOCK_ACCESS_FS_REFER
    | _LANDLOCK_ACCESS_FS_TRUNCATE
)


class _LandlockRulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _LandlockPathBeneath(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int)]


class LandlockUnavailable(RuntimeError):
    """The kernel cannot provide the required inherited filesystem boundary."""


@dataclass(frozen=True)
class _LandlockPath:
    path: Path
    allowed_access: int


class LandlockPolicy:
    """An inherited filesystem allowlist for OpenCode and its descendants."""

    def __init__(self, argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> None:
        self._paths = self._paths_for(argv, cwd, env)

    def apply(self) -> None:
        """Install the policy in the child before it can execute OpenCode."""

        _set_no_new_privs()
        attr = _LandlockRulesetAttr(_LANDLOCK_ACCESS_FS_ALL)
        ruleset_fd = _landlock_syscall(
            _LANDLOCK_CREATE_RULESET,
            ctypes.byref(attr),
            ctypes.sizeof(attr),
            0,
        )
        if ruleset_fd < 0:
            raise LandlockUnavailable("the OpenCode filesystem boundary could not be created")
        try:
            for item in self._paths:
                try:
                    parent_fd = os.open(item.path, os.O_PATH | os.O_CLOEXEC)
                except OSError as exc:
                    raise LandlockUnavailable("the OpenCode filesystem boundary path could not be opened") from exc
                try:
                    beneath = _LandlockPathBeneath(item.allowed_access, parent_fd)
                    result = _landlock_syscall(
                        _LANDLOCK_ADD_RULE,
                        ruleset_fd,
                        _LANDLOCK_RULE_TYPE_PATH_BENEATH,
                        ctypes.byref(beneath),
                        0,
                    )
                    if result < 0:
                        raise LandlockUnavailable("the OpenCode filesystem boundary rule could not be installed")
                finally:
                    os.close(parent_fd)
            if _landlock_syscall(_LANDLOCK_RESTRICT_SELF, ruleset_fd, 0, 0) < 0:
                raise LandlockUnavailable("the OpenCode filesystem boundary could not be enforced")
        finally:
            os.close(ruleset_fd)

    @staticmethod
    def _paths_for(argv: Sequence[str], cwd: Path, env: Mapping[str, str]) -> tuple[_LandlockPath, ...]:
        temporary_root = Path(tempfile.gettempdir()).resolve()
        try:
            resolved_cwd = cwd.resolve(strict=True)
        except OSError as exc:
            raise LandlockUnavailable("the OpenCode workdir could not be resolved") from exc
        if not _is_below(resolved_cwd, temporary_root):
            raise LandlockUnavailable("OpenCode must run inside a disposable temporary workdir")

        paths: dict[Path, int] = {}

        def add(path: Path, access: int) -> None:
            try:
                resolved = path.resolve(strict=True)
            except OSError as exc:
                raise LandlockUnavailable("the OpenCode filesystem boundary path could not be resolved") from exc
            paths[resolved] = paths.get(resolved, 0) | access

        def add_ancestors(path: Path) -> None:
            current = path
            while True:
                parent = current.parent
                if parent == current:
                    return
                paths[parent] = paths.get(parent, 0) | _LANDLOCK_ACCESS_FS_READ_DIR
                current = parent

        add(resolved_cwd, _LANDLOCK_ACCESS_FS_ALL)
        add_ancestors(resolved_cwd)

        for system_path in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/dev", "/proc", "/run"):
            candidate = Path(system_path)
            if candidate.exists():
                add(candidate, _LANDLOCK_ACCESS_FS_READ_EXECUTE | _LANDLOCK_ACCESS_FS_READ_DIR)
                add_ancestors(candidate)
        if Path("/dev/null").exists():
            add(Path("/dev/null"), _LANDLOCK_ACCESS_FS_READ_FILE | _LANDLOCK_ACCESS_FS_WRITE_FILE)

        if not argv or not isinstance(argv[0], str) or not argv[0]:
            raise LandlockUnavailable("the OpenCode executable is empty")
        executable = Path(argv[0])
        if not executable.is_absolute():
            raise LandlockUnavailable("the OpenCode executable must be an absolute path")
        add(executable.parent, _LANDLOCK_ACCESS_FS_READ_EXECUTE | _LANDLOCK_ACCESS_FS_READ_DIR)
        add_ancestors(executable.parent)
        if executable.parent.name in {"bin", "sbin"}:
            add(executable.parent.parent, _LANDLOCK_ACCESS_FS_READ_EXECUTE | _LANDLOCK_ACCESS_FS_READ_DIR)
            add_ancestors(executable.parent.parent)
        with contextlib.suppress(OSError):
            resolved_executable = executable.resolve(strict=True)
            add(resolved_executable.parent, _LANDLOCK_ACCESS_FS_READ_EXECUTE | _LANDLOCK_ACCESS_FS_READ_DIR)
            add_ancestors(resolved_executable.parent)
            if resolved_executable.parent.name in {"bin", "sbin"}:
                add(
                    resolved_executable.parent.parent,
                    _LANDLOCK_ACCESS_FS_READ_EXECUTE | _LANDLOCK_ACCESS_FS_READ_DIR,
                )
                add_ancestors(resolved_executable.parent.parent)

        for name in ("HOME", "XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_STATE_HOME", "XDG_CACHE_HOME", "TMPDIR"):
            value = env.get(name)
            if not isinstance(value, str) or not value:
                continue
            candidate = Path(value)
            if not candidate.is_absolute():
                raise LandlockUnavailable(f"the OpenCode {name} path must be absolute")
            resolved = candidate.resolve(strict=False)
            if not _is_below(resolved, temporary_root):
                continue
            if resolved.exists():
                add(resolved, _LANDLOCK_ACCESS_FS_ALL)
                add_ancestors(resolved)

        for value in env.get("PATH", "").split(os.pathsep):
            if not value:
                continue
            candidate = Path(value)
            if candidate.is_absolute() and candidate.exists():
                add(candidate, _LANDLOCK_ACCESS_FS_READ_EXECUTE | _LANDLOCK_ACCESS_FS_READ_DIR)
                add_ancestors(candidate)

        config = env.get("OPENCODE_CONFIG")
        if isinstance(config, str) and config:
            candidate = Path(config)
            if not candidate.is_absolute():
                raise LandlockUnavailable("the OpenCode config path must be absolute")
            if candidate.exists():
                add(candidate.parent, _LANDLOCK_ACCESS_FS_READ_EXECUTE | _LANDLOCK_ACCESS_FS_READ_DIR)
                add_ancestors(candidate.parent)

        return tuple(_LandlockPath(path, access) for path, access in paths.items())


def _is_below(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _landlock_syscall(number: int, *arguments: object) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    result = syscall(number, *arguments)
    return int(result)


def landlock_version() -> int:
    version = _landlock_syscall(_LANDLOCK_CREATE_RULESET, None, 0, _LANDLOCK_CREATE_RULESET_VERSION)
    if version < 0:
        return 0
    return version


def _set_no_new_privs() -> None:
    libc = ctypes.CDLL(None, use_errno=True)
    result = libc.prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
    if result != 0:
        raise LandlockUnavailable("OpenCode could not disable privilege escalation")


def require_landlock() -> None:
    if not os.name == "posix" or landlock_version() < 3:
        raise LandlockUnavailable("OpenCode requires Linux Landlock ABI 3 for filesystem confinement")


__all__ = ["LandlockPolicy", "LandlockUnavailable", "landlock_version", "require_landlock"]
