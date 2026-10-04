"""Standalone Landlock layer used by OpenCode's model-tool shell wrappers.

This file is copied into the disposable tool directory and executed by the system Python
interpreter.  It must not import Blizzard: the outer OpenCode Landlock policy intentionally does
not grant the model-tool process access to the runner's source tree.
"""

from __future__ import annotations

import ctypes
import os
import sys
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


class _RulesetAttr(ctypes.Structure):
    _fields_ = [("handled_access_fs", ctypes.c_uint64)]


class _PathBeneath(ctypes.Structure):
    _fields_ = [("allowed_access", ctypes.c_uint64), ("parent_fd", ctypes.c_int)]


def _syscall(number: int, *arguments: object) -> int:
    libc = ctypes.CDLL(None, use_errno=True)
    syscall = libc.syscall
    syscall.restype = ctypes.c_long
    return int(syscall(number, *arguments))


def _is_below(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _apply_tool_layer(cwd: Path, shell: Path, env: dict[str, str]) -> None:
    # TMPDIR is deliberately changed to a child-private directory below.  The outer disposable
    # boundary is supplied separately because ``tempfile.gettempdir()`` would resolve TMPDIR again.
    temporary_root = Path(env.get("BLIZZARD_OPENCODE_TEMP_ROOT", "/tmp")).resolve()
    cwd = cwd.resolve(strict=True)
    if not _is_below(cwd, temporary_root):
        raise RuntimeError("the model tool workdir is not disposable")
    shell = shell.resolve(strict=True)

    paths: dict[Path, int] = {}

    def add(path: Path, access: int) -> None:
        resolved = path.resolve(strict=True)
        paths[resolved] = paths.get(resolved, 0) | access

    def add_ancestors(path: Path) -> None:
        current = path
        while True:
            parent = current.parent
            if parent == current:
                return
            paths[parent] = paths.get(parent, 0) | _LANDLOCK_ACCESS_FS_READ_DIR
            current = parent

    add(cwd, _LANDLOCK_ACCESS_FS_ALL)
    add_ancestors(cwd)
    for system_path in ("/usr", "/bin", "/sbin", "/lib", "/lib64", "/etc", "/dev", "/proc", "/run"):
        candidate = Path(system_path)
        if candidate.exists():
            add(candidate, _LANDLOCK_ACCESS_FS_READ_EXECUTE | _LANDLOCK_ACCESS_FS_READ_DIR)
            add_ancestors(candidate)
    if Path("/dev/null").exists():
        add(Path("/dev/null"), _LANDLOCK_ACCESS_FS_READ_FILE | _LANDLOCK_ACCESS_FS_WRITE_FILE)
    if shell.parent.exists():
        add(shell.parent, _LANDLOCK_ACCESS_FS_READ_EXECUTE | _LANDLOCK_ACCESS_FS_READ_DIR)
        add_ancestors(shell.parent)

    tempdir = env.get("TMPDIR")
    if tempdir:
        candidate = Path(tempdir).resolve(strict=True)
        if _is_below(candidate, temporary_root):
            add(candidate, _LANDLOCK_ACCESS_FS_ALL)
            add_ancestors(candidate)

    no_new_privs = ctypes.CDLL(None, use_errno=True).prctl(_PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0)
    if no_new_privs != 0:
        raise RuntimeError("the model tool could not disable privilege escalation")
    ruleset_fd = _syscall(
        _LANDLOCK_CREATE_RULESET,
        ctypes.byref(_RulesetAttr(_LANDLOCK_ACCESS_FS_ALL)),
        ctypes.sizeof(_RulesetAttr),
        0,
    )
    if ruleset_fd < 0:
        raise RuntimeError("the model tool filesystem layer could not be created")
    try:
        for path, access in paths.items():
            parent_fd = os.open(path, os.O_PATH | os.O_CLOEXEC)
            try:
                beneath = _PathBeneath(access, parent_fd)
                if (
                    _syscall(
                        _LANDLOCK_ADD_RULE,
                        ruleset_fd,
                        _LANDLOCK_RULE_TYPE_PATH_BENEATH,
                        ctypes.byref(beneath),
                        0,
                    )
                    < 0
                ):
                    raise RuntimeError("the model tool filesystem rule could not be installed")
            finally:
                os.close(parent_fd)
        if _syscall(_LANDLOCK_RESTRICT_SELF, ruleset_fd, 0, 0, 0) < 0:
            raise RuntimeError("the model tool filesystem layer could not be enforced")
    finally:
        os.close(ruleset_fd)


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("the model tool shell is missing")
    shell = Path(sys.argv[1])
    if not shell.is_absolute():
        raise SystemExit("the model tool shell must be absolute")
    env = dict(os.environ)
    _apply_tool_layer(Path.cwd(), shell, env)
    env.update(
        {
            "HOME": "/dev/null",
            "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        }
    )
    for name in tuple(env):
        if name.startswith("BLIZZARD_OPENCODE_") or name.startswith("OPENCODE_") or name.startswith("XDG_"):
            del env[name]
    os.execve(str(shell), [str(shell), *sys.argv[2:]], env)


if __name__ == "__main__":
    try:
        main()
    except (OSError, RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
