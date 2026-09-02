"""Sanitized evidence export for the OpenCode compatibility diagnostic."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from blizzard.runner.harness.compatibility import CompatibilityReport
from blizzard.runner.harness.internal.opencode_sanitizer import OpenCodeSanitizer, is_absolute_host_path


class OpenCodeEvidenceError(RuntimeError):
    """Evidence could not be written as a sanitized JSON document."""


@dataclass(frozen=True)
class OpenCodeEvidence:
    """Write the report and process observations into a caller-selected evidence directory."""

    directory: Path
    secrets: Sequence[str] = ()
    path_replacements: Sequence[tuple[str, str]] = ()

    def validate(self) -> None:
        """Validate the destination without creating it or running the diagnostic."""

        try:
            if not str(self.directory).strip():
                raise OpenCodeEvidenceError("the compatibility evidence directory is empty")
            if self.directory.exists():
                if not self.directory.is_dir():
                    raise OpenCodeEvidenceError("the compatibility evidence path is not a directory")
                if not os.access(self.directory, os.W_OK | os.X_OK):
                    raise OpenCodeEvidenceError("the compatibility evidence directory is not writable")
                return

            parent = self.directory.parent
            while not parent.exists() and parent != parent.parent:
                parent = parent.parent
            if not parent.is_dir() or not os.access(parent, os.W_OK | os.X_OK):
                raise OpenCodeEvidenceError("the compatibility evidence directory has no writable parent")
        except (OSError, ValueError) as exc:
            raise OpenCodeEvidenceError("the compatibility evidence directory is not usable") from exc

    def write(self, report: CompatibilityReport, runtime: Mapping[str, object]) -> tuple[Path, Path]:
        """Persist only sanitized JSON and return the two retained paths."""

        self.validate()
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            scratch = runtime.get("scratch_workdir")
            scratch_replacement = ((str(scratch), "<scratch>"),) if isinstance(scratch, str) and scratch else ()
            isolated_paths = runtime.get("isolated_paths")
            isolation_root = isolated_paths.get("root") if isinstance(isolated_paths, Mapping) else None
            isolation_replacement = (
                ((str(isolation_root), "<isolation>"),) if isinstance(isolation_root, str) and isolation_root else ()
            )
            replacements = (
                *self.path_replacements,
                *_runtime_identifier_replacements(runtime),
                *_runtime_path_replacements(runtime),
                *scratch_replacement,
                *isolation_replacement,
                (str(self.directory), "<evidence>"),
            )
            sanitizer = OpenCodeSanitizer(secrets=tuple(self.secrets), path_replacements=replacements)
            report_path = self.directory / "report.json"
            runtime_path = self.directory / "runtime.json"
            _write_json(report_path, sanitizer.value(report.to_payload()))
            _write_json(runtime_path, sanitizer.value(dict(runtime)))
            return report_path, runtime_path
        except (OSError, TypeError, ValueError) as exc:
            raise OpenCodeEvidenceError("compatibility evidence could not be written") from exc


def _write_json(path: Path, value: object) -> None:
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _runtime_path_replacements(runtime: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    operations = runtime.get("operations")
    if not isinstance(operations, Sequence) or isinstance(operations, (str, bytes, bytearray)):
        return ()

    replacements: list[tuple[str, str]] = []
    scratch = runtime.get("scratch_workdir")
    scratch_path = str(scratch).rstrip("/\\") if isinstance(scratch, str) and scratch else None
    for operation in operations:
        if not isinstance(operation, Mapping):
            continue
        argv = operation.get("argv")
        if isinstance(argv, Sequence) and not isinstance(argv, (str, bytes, bytearray)) and argv:
            binary = argv[0]
            if isinstance(binary, str) and is_absolute_host_path(binary):
                replacements.append((binary, "<binary>"))
        cwd = operation.get("cwd")
        if not isinstance(cwd, str) or not is_absolute_host_path(cwd):
            continue
        if scratch_path is None or not (
            cwd == scratch_path or cwd.startswith((scratch_path + "/", scratch_path + "\\"))
        ):
            replacements.append((cwd, "<workdir>"))
    return tuple(replacements)


_RAW_IDENTIFIER_RE = re.compile(r"\b(?P<kind>ses|msg|prt|call)_[A-Za-z0-9]+\b")
_OBSERVED_IDENTIFIER_KEYS = {
    "session_id": "session",
    "sessionid": "session",
    "message_id": "message",
    "messageid": "message",
    "part_id": "part",
    "partid": "part",
    "call_id": "call",
    "callid": "call",
    "parent_id": "session",
    "parentid": "session",
}


def _runtime_identifier_replacements(runtime: Mapping[str, object]) -> tuple[tuple[str, str], ...]:
    serialized = json.dumps(runtime, ensure_ascii=False)
    observed: dict[str, set[str]] = {kind: set() for kind in ("session", "message", "part", "call")}

    def collect(value: object, context_kind: str | None = None) -> None:
        if isinstance(value, Mapping):
            for key, nested in value.items():
                key_name = re.sub(r"[-.]", "_", key.lower()) if isinstance(key, str) else ""
                kind = _OBSERVED_IDENTIFIER_KEYS.get(key_name, context_kind)
                if isinstance(nested, str) and kind in observed and nested:
                    observed[kind].add(nested)
                collect(nested, kind)
        elif isinstance(value, (list, tuple)):
            for nested in value:
                collect(nested, context_kind)

    collect(runtime)
    for match in _RAW_IDENTIFIER_RE.finditer(serialized):
        kind = {"ses": "session", "msg": "message", "prt": "part", "call": "call"}[match.group("kind")]
        identifier = match.group(0)
        # A key name such as `call_id` matches the raw-identifier shape; it is not one.
        if identifier not in _OBSERVED_IDENTIFIER_KEYS:
            observed[kind].add(identifier)
    replacements: list[tuple[str, str]] = []
    for kind in ("session", "message", "part", "call"):
        for index, identifier in enumerate(sorted(observed[kind]), start=1):
            replacements.append((identifier, f"<{kind}-{index}>"))
    return tuple(replacements)


__all__ = ["OpenCodeEvidence", "OpenCodeEvidenceError"]
