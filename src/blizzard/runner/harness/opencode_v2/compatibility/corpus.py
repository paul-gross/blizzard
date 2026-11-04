"""Sanitized evidence for the OpenCode 2 proof: the report, one fixture per capture, and the manifest a committed
corpus carries. No credential, and no path under the operator's home or the proof's scratch, reaches a file."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from blizzard.foundation.roles import collaborator, domain_model
from blizzard.runner.harness.json_value import JsonObject, JsonValue
from blizzard.runner.harness.opencode_v2.compatibility.rows import DECLARED_DEGRADATIONS, ProofReport, classify_row

#: Keys whose values may carry a credential: a model's provider ``settings`` can hold an API key.
REDACTED_KEYS = frozenset({"settings", "apikey", "apiKey", "password", "authorization", "token", "secret"})
#: Captures settled from 2.1.4's source rather than a live run.
SOURCE_CAPTURES = frozenset({"error_shapes_from_source"})
#: Captures that belong to the OpenCode 1 corpus.
OPENCODE1_CAPTURES = frozenset({"store_migration"})


class CorpusError(RuntimeError):
    """Evidence could not be written."""


@domain_model
@dataclass(frozen=True)
class Sanitizer:
    secrets: Sequence[str]
    replacements: Sequence[tuple[str, str]]

    def text(self, value: str) -> str:
        for secret in self.secrets:
            if secret:
                value = value.replace(secret, "<redacted>")
        for original, placeholder in sorted(self.replacements, key=lambda pair: -len(pair[0])):
            if original:
                value = value.replace(original, placeholder)
        return value

    def value(self, value: JsonValue) -> JsonValue:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, list):
            return [self.value(item) for item in value]
        if isinstance(value, dict):
            return {
                self.text(key): "<redacted>" if key in REDACTED_KEYS else self.value(item)
                for key, item in value.items()
            }
        return value


def _write(path: Path, value: JsonValue) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")


def validate_evidence_directory(directory: Path) -> None:
    """Refuse an evidence path that cannot become a directory — checked before a live run, not after it."""
    if directory.exists() and not directory.is_dir():
        raise CorpusError(f"the evidence path is not a directory: {directory}")


@collaborator
@dataclass(frozen=True)
class ProofEvidence:
    """Writes into a caller-named directory: the OpenCode 2 corpus at its top, the OpenCode 1 store fixture under
    ``opencode1/``."""

    directory: Path
    sanitizer: Sanitizer

    def validate(self) -> None:
        validate_evidence_directory(self.directory)

    def write(self, report: ProofReport, captures: Mapping[str, JsonValue], *, model: str) -> Path:
        self.validate()
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            opencode1 = self.directory / "opencode1"
            fixtures: list[JsonValue] = []
            for name, value in sorted(captures.items()):
                target = opencode1 if name in OPENCODE1_CAPTURES else self.directory
                target.mkdir(exist_ok=True)
                _write(target / f"{name}.json", self.sanitizer.value(value))
                if name not in OPENCODE1_CAPTURES:
                    fixtures.append({"path": f"{name}.json", "source": "source" if name in SOURCE_CAPTURES else "live"})
            payload = self.sanitizer.value(report.payload())
            _write(self.directory / "report.json", payload)
            _write(self.directory / "manifest.json", self.manifest(report, fixtures, model=model))
        except OSError as exc:
            raise CorpusError(f"compatibility evidence could not be written: {exc}") from exc
        return self.directory

    def manifest(self, report: ProofReport, fixtures: list[JsonValue], *, model: str) -> JsonObject:
        return {
            "harness": "opencode",
            "major_version": 2,
            "version": report.observed_version,
            "admitted_range": report.admitted_range,
            "sanitized": True,
            "model": self.sanitizer.text(model),
            # The pinned classification sits where every committed corpus manifest records it.
            "live_evidence": {
                "version": report.observed_version,
                "classification": report.classification().value,
                "admissible": report.admissible(),
                "sanitized": True,
            },
            "rows": [
                {
                    "name": observation.row.value,
                    "classification": classify_row(observation).value,
                    "evidence": list(observation.evidence),
                }
                for observation in report.observations
            ],
            "declared_degradations": [
                {"row": degradation.row.value, "summary": degradation.summary} for degradation in DECLARED_DEGRADATIONS
            ],
            "fixtures": fixtures,
        }


__all__ = [
    "OPENCODE1_CAPTURES",
    "REDACTED_KEYS",
    "SOURCE_CAPTURES",
    "CorpusError",
    "ProofEvidence",
    "Sanitizer",
    "validate_evidence_directory",
]
