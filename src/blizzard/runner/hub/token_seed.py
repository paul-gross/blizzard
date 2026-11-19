"""The seed's token import: each kept runner with no stored token has the one its deprecated sources hold sealed as its
first row, before its settings are seeded and it is projected.

The import is one insert-where-absent per runner, so it never overwrites a row — one that does not open included —
and a start killed anywhere in it is completed by the next: a runner already imported keeps its row, and one not yet
imported is imported then."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.crash import CrashPoint
from blizzard.foundation.roles import domain_model
from blizzard.foundation.secrets.value import SecretValue
from blizzard.runner.hub.sealed_tokens import HubTokens, StoredToken

#: A runner's token row is written; its settings seed and projection are not.
TOKEN_IMPORTED = CrashPoint.declare(
    "seed.after-token.before-settings", "a runner's token is sealed in the store; its settings are not yet seeded"
)
#: One runner is imported, seeded and projected; the next runner's import has not begun.
RUNNER_SEEDED = CrashPoint.declare(
    "seed.after-runner.before-next-runner", "one runner is seeded whole; the next runner's token is not yet imported"
)


@domain_model
@dataclass(frozen=True)
class TokenImport:
    """What the import left a runner with: its stored-token outcome, and whether this import wrote the row."""

    stored: StoredToken
    imported: bool


def import_hub_token(tokens: HubTokens, runner_id: str, resolved: str) -> TokenImport:
    """Seal ``resolved`` — the token the deprecated sources hold, ``""`` when none does — as the runner's first row
    unless it holds one, and answer the row it is left with."""
    imported = bool(resolved) and tokens.seal_if_absent(runner_id, SecretValue(resolved))
    TOKEN_IMPORTED.reached()
    return TokenImport(tokens.stored(runner_id), imported)
