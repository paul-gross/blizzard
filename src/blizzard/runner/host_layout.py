"""A host directory's layout, its lock, and every write to ``blizzard-host.toml``.

The layout is the directory's filenames and its graduation state; the lock is the one owner a host
directory has; the writes are the few the host file takes after its author's — ``init``'s scaffold and its
runner ``id``, and ``prompt install`` repointing a workspace's prompt keys. Each write is a targeted line
edit of the file as it stands, never a regeneration, so an operator's comments and ordering survive."""

from __future__ import annotations

import copy
import fcntl
import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import TracebackType
from typing import Any

from blizzard.foundation.roles import collaborator, domain_model
from blizzard.runner.config_table import ConfigError

HOST_CONFIG_FILENAME = "blizzard-host.toml"
DATA_DIRNAME = "data"
HOST_STORE_FILENAME = "host.db"
HOST_LOCK_FILENAME = "host.lock"
#: The host's local API socket, in the host directory itself.
HOST_SOCKET_FILENAME = "host.sock"

#: What a directory that has not graduated still holds.
LEGACY_CONFIG_FILENAME = "blizzard-runner.toml"
LEGACY_STORE_FILENAME = "runner.db"
LEGACY_SOCKET_FILENAME = "runner.sock"

#: The verb that creates or adopts a host directory's store; it takes the directory as ``--dir``.
MIGRATE_COMMAND = "blizzard runner migrate"

_OWNER_ONLY = 0o600
_PID_BYTES = 32


def migrate_remedy(root: Path) -> str:
    """The runnable command that migrates the host directory at ``root``."""
    return f"{MIGRATE_COMMAND} --dir {root}"


class Graduation(StrEnum):
    """Whether a directory is a host: it holds ``blizzard-host.toml`` (``GRADUATED``), holds only the
    pre-host ``blizzard-runner.toml`` (``UNGRADUATED``), or holds neither (``UNINITIALISED``)."""

    GRADUATED = "graduated"
    UNGRADUATED = "ungraduated"
    UNINITIALISED = "uninitialised"


@domain_model
@dataclass(frozen=True)
class HostLayout:
    """The files a host directory holds, named from its root."""

    root: Path

    @property
    def config_path(self) -> Path:
        return self.root / HOST_CONFIG_FILENAME

    @property
    def data_dir(self) -> Path:
        return self.root / DATA_DIRNAME

    @property
    def store_path(self) -> Path:
        return self.data_dir / HOST_STORE_FILENAME

    @property
    def default_db_url(self) -> str:
        return f"sqlite:///{self.store_path.resolve()}"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / HOST_LOCK_FILENAME

    @property
    def socket_path(self) -> Path:
        return self.root / HOST_SOCKET_FILENAME

    @property
    def legacy_config_path(self) -> Path:
        return self.root / LEGACY_CONFIG_FILENAME

    @property
    def legacy_store_path(self) -> Path:
        return self.data_dir / LEGACY_STORE_FILENAME

    @property
    def legacy_socket_path(self) -> Path:
        return self.root / LEGACY_SOCKET_FILENAME

    def graduation(self) -> Graduation:
        if self.config_path.exists():
            return Graduation.GRADUATED
        if self.legacy_config_path.exists():
            return Graduation.UNGRADUATED
        return Graduation.UNINITIALISED


class HostHeld(RuntimeError):
    """Another process holds the host directory's lock; ``pid`` is the holder's, when it recorded one."""

    def __init__(self, lock_path: Path, pid: int | None) -> None:
        self.pid = pid
        holder = f"pid {pid}" if pid is not None else "another process"
        super().__init__(f"the host directory is held by {holder} ({lock_path})")


@collaborator
@dataclass
class HostLock:
    """The exclusive, non-blocking ``flock`` on ``data/host.lock``, held until :meth:`release` — or the
    process's end, which the kernel releases it at. The file names the holder's pid."""

    path: Path
    fd: int

    @classmethod
    def acquire(cls, layout: HostLayout) -> HostLock:
        """Take the lock, or raise :class:`HostHeld` naming the pid its holder recorded."""
        layout.data_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(layout.lock_path, os.O_RDWR | os.O_CREAT, _OWNER_ONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            recorded = os.pread(fd, _PID_BYTES, 0).decode(errors="replace").strip()
            os.close(fd)
            raise HostHeld(layout.lock_path, int(recorded) if recorded.isdigit() else None) from None
        os.ftruncate(fd, 0)
        os.pwrite(fd, str(os.getpid()).encode(), 0)
        return cls(layout.lock_path, fd)

    def release(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> HostLock:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.release()


def write_scaffold(layout: HostLayout, text: str) -> None:
    """Create ``blizzard-host.toml`` holding ``text``, whole or not at all; raises ``FileExistsError``
    when the file is already there."""
    layout.root.mkdir(parents=True, exist_ok=True)
    pending = _write_pending(layout.root, text, mode=_OWNER_ONLY)
    try:
        os.link(pending, layout.config_path)
    finally:
        Path(pending).unlink()


def write_runner_id(layout: HostLayout, runner_name: str, runner_id: str) -> None:
    """Set the ``id`` of the ``[[runner]]`` named ``runner_name``, replacing one it holds and inserting
    one it lacks. Re-running with the same arguments changes nothing."""
    _set_table_keys(layout, "runner", "name", runner_name, {"id": json.dumps(runner_id)})


def repoint_workspace_prompt(layout: HostLayout, workspace_id: str, *, file_path: str) -> None:
    """Point the ``[[workspace]]`` ``workspace_id`` at an installed prompt copy: ``prompt_file`` names it
    and ``prompt_package`` is cleared, since the two are exclusive."""
    _set_table_keys(
        layout,
        "workspace",
        "id",
        workspace_id,
        {"prompt_file": json.dumps(file_path), "prompt_package": '""'},
    )


def _set_table_keys(layout: HostLayout, table: str, identity_key: str, identity: str, values: dict[str, str]) -> None:
    text = layout.config_path.read_text()
    try:
        before = tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{layout.config_path} is not valid TOML: {error}") from error
    entries = before.get(table, [])
    position = next(
        (i for i, entry in enumerate(entries) if isinstance(entry, dict) and entry.get(identity_key) == identity), None
    )
    if position is None:
        raise ConfigError(f"{layout.config_path} has no [[{table}]] with {identity_key} = {identity!r}")
    lines = text.splitlines(keepends=True)
    statements = _statements(lines)
    start, end = _table_body(lines, statements, table, position)
    pending = dict(values)
    # Back to front, so a replaced multi-line value leaves the earlier line numbers alone.
    for first, last in reversed([span for span in statements if start <= span[0] < end]):
        key = lines[first].split("=", 1)[0].strip()
        if key in pending and "=" in lines[first]:
            lines[first : last + 1] = [f"{key} = {pending.pop(key)}\n"]
            end -= last - first
    if pending:
        # After the table's last statement, rewinding off the blank lines and comments that lead into the next header.
        at = end
        while at > start and (not lines[at - 1].strip() or lines[at - 1].lstrip().startswith("#")):
            at -= 1
        if at > 0 and not lines[at - 1].endswith("\n"):
            lines[at - 1] += "\n"
        lines[at:at] = [f"{key} = {value}\n" for key, value in pending.items()]
    edited = "".join(lines)
    try:
        after = tomllib.loads(edited)
    except tomllib.TOMLDecodeError as error:
        raise ConfigError(f"{layout.config_path} cannot take the edit to its [[{table}]]: {error}") from error
    if after != _with_values(before, table, position, values):
        raise ConfigError(
            f"{layout.config_path} cannot take the edit to its [[{table}]] {identity!r} safely; edit it by hand"
        )
    if edited == text:
        return
    replacement = _write_pending(layout.root, edited, mode=layout.config_path.stat().st_mode & 0o777)
    os.replace(replacement, layout.config_path)


def _with_values(parsed: dict[str, Any], table: str, position: int, values: dict[str, str]) -> dict[str, Any]:
    """``parsed`` with the ``position``-th ``[[table]]`` holding ``values``, TOML literals: what the edit must yield."""
    expected = copy.deepcopy(parsed)
    for key, literal in values.items():
        expected[table][position][key] = tomllib.loads(f"v = {literal}")["v"]
    return expected


_HEADER = re.compile(r"^\s*\[")


def _statements(lines: list[str]) -> list[tuple[int, int]]:
    """The ``(first, last)`` line of each header and ``key = value`` statement, found by scanning past comments,
    quoted strings (multi-line ones included) and bracketed multi-line values, so a ``[``-led line inside one
    is not taken for a header."""
    found: list[tuple[int, int]] = []
    depth = 0
    quote = ""  # the delimiter of the string being scanned (one or three quote characters); empty outside one
    first = 0
    for number, line in enumerate(lines):
        if depth == 0 and not quote:
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            first = number
        i = 0
        while i < len(line):
            if quote:
                if quote[0] == '"' and line[i] == "\\":
                    i += 2
                    continue
                if line.startswith(quote, i):
                    i += len(quote)
                    quote = ""
                    continue
                i += 1
                continue
            char = line[i]
            if char == "#":
                break
            if char in "\"'":
                quote = char * 3 if line.startswith(char * 3, i) else char
                i += len(quote)
                continue
            if char in "[{":
                depth += 1
            elif char in "]}":
                depth -= 1
            i += 1
        if len(quote) == 1:
            quote = ""  # a single-line string ends with its line
        if depth == 0 and not quote:
            found.append((first, number))
    return found


def _table_body(lines: list[str], statements: list[tuple[int, int]], table: str, position: int) -> tuple[int, int]:
    """The line range after the ``position``-th ``[[table]]`` header, up to the next header of any kind."""
    opening = re.compile(rf"^\s*\[\[\s*{re.escape(table)}\s*\]\]\s*(#.*)?$")
    seen = -1
    for index, (first, last) in enumerate(statements):
        if first == last and opening.match(lines[first]):
            seen += 1
            if seen == position:
                end = next((f for f, _ in statements[index + 1 :] if _HEADER.match(lines[f])), len(lines))
                return last + 1, end
    raise ConfigError(f"[[{table}]] number {position + 1} has no header line to edit")


def _write_pending(directory: Path, text: str, *, mode: int) -> str:
    """``text`` written to a temp file beside its destination, so a rename or link makes it appear whole."""
    fd, pending = tempfile.mkstemp(dir=directory, prefix=f"{HOST_CONFIG_FILENAME}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(pending, mode)
    except BaseException:
        Path(pending).unlink(missing_ok=True)
        raise
    return pending
