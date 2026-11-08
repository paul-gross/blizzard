"""The OpenCode 2 price-catalog seam (``bzh:pluggable-seams``).

OpenCode 2 keeps a models catalog in its own SQLite store; this binding translates a model id and one step's tokens
into a dollar estimate under the catalog's own context-override rule. Blizzard keeps no price table, and deciding when
a step needs an estimate stays with the caller of :class:`IOpenCodeTwoPriceCatalog`. It is kept parallel to the
OpenCode 1 price cache and never imports it: one major version's binding may not import the other's."""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from urllib.parse import quote

from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.runner.harness.json_value import JsonValue, decode_json
from blizzard.runner.harness.opencode_v2.shapes import Tokens

_log = get_logger("blizzard.runner.harness")

#: The ``kv`` key OpenCode stores the catalog under for its default catalog URL.
CATALOG_KEY = "models-dev:v2:catalog"

#: The catalog's cost schema is dollars per 1,000,000 tokens.
_TOKENS_PER_RATE_UNIT = 1_000_000.0

#: How long a read waits on a live OpenCode service writing the store, in seconds.
_BUSY_TIMEOUT_SECONDS = 0.25


@domain_model
@dataclass(frozen=True)
class _ContextOverride:
    """An override applying once the prompt size passes ``threshold`` (inclusive when ``inclusive``)."""

    threshold: float
    inclusive: bool
    price: float | None
    cache_read: float | None
    cache_write: float | None

    def applies(self, prompt_size: float) -> bool:
        return prompt_size >= self.threshold if self.inclusive else prompt_size > self.threshold


@domain_model
@dataclass(frozen=True)
class _RateLadder:
    """One token class's base price and its context overrides."""

    price: float
    cache_read: float = 0.0
    cache_write: float = 0.0
    overrides: tuple[_ContextOverride, ...] = ()

    def at(self, prompt_size: float) -> _RateLadder:
        applying = [override for override in self.overrides if override.applies(prompt_size)]
        if not applying:
            return self
        chosen = max(applying, key=lambda override: override.threshold)
        return _RateLadder(
            price=self.price if chosen.price is None else chosen.price,
            cache_read=self.cache_read if chosen.cache_read is None else chosen.cache_read,
            cache_write=self.cache_write if chosen.cache_write is None else chosen.cache_write,
        )


@domain_model
@dataclass(frozen=True)
class OpenCodeTwoModelPrice:
    """One model's ``cost.text`` entry, priceable because it carries both an ``input`` and an ``output`` price."""

    input: _RateLadder
    output: _RateLadder
    #: A cache-write rate keyed by TTL: a step that wrote cache tokens cannot be priced.
    cache_write_by_ttl: bool = False

    def estimate(self, tokens: Tokens) -> float | None:
        """This step's estimated cost in dollars, every token class at the rate its own override selects for the
        step's prompt size; ``None`` when the step wrote cache tokens at a TTL-keyed rate. Reasoning is billed as
        output."""
        if self.cache_write_by_ttl and tokens.cache_write:
            return None
        prompt_size = tokens.input + tokens.cache_read + tokens.cache_write
        in_rate = self.input.at(prompt_size)
        out_rate = self.output.at(prompt_size)
        return (
            tokens.input * in_rate.price
            + (tokens.output + tokens.reasoning) * out_rate.price
            + tokens.cache_read * in_rate.cache_read
            + tokens.cache_write * in_rate.cache_write
        ) / _TOKENS_PER_RATE_UNIT


class IOpenCodeTwoPriceCatalog(Protocol):
    """The one-method price-lookup seam (``bzh:seam-size-ceiling``)."""

    def price_for(self, provider: str, model: str) -> OpenCodeTwoModelPrice | None:
        """``provider``'s ``model``'s price, or ``None`` when the catalog has no priceable entry for it: a missing
        store or row, a malformed value, an unknown model, or a rate that cannot be priced. Never raises."""
        ...


class SqliteOpenCodeTwoPriceCatalog:
    """Reads OpenCode 2's own catalog row from its SQLite store at ``path``, read-only so a live service keeps
    writing it. Looks one model up with SQLite's JSON functions rather than decoding the whole catalog."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def price_for(self, provider: str, model: str) -> OpenCodeTwoModelPrice | None:
        raw = self._read_cost(provider, model)
        if raw is None:
            return None
        price = _parse_price(raw)
        if price is None:
            _log.warning("opencode 2 price catalog entry is unpriceable", provider=provider, model=model)
        return price

    def _read_cost(self, provider: str, model: str) -> JsonValue | None:
        if '"' in provider or '"' in model or "\\" in provider or "\\" in model:
            return None
        json_path = f'$."{provider}"."models"."{model}"."cost"."text"'
        try:
            connection = sqlite3.connect(
                f"file:{quote(str(self._path))}?mode=ro", uri=True, timeout=_BUSY_TIMEOUT_SECONDS
            )
            try:
                row = connection.execute(
                    "select json_extract(json_extract(value, '$.body'), ?) from kv where key = ?",
                    (json_path, CATALOG_KEY),
                ).fetchone()
            finally:
                connection.close()
        except sqlite3.Error as exc:
            _log.warning("opencode 2 price catalog is unreadable", path=str(self._path), detail=str(exc))
            return None
        if row is None or not isinstance(row[0], str):
            _log.warning("opencode 2 price catalog has no entry for model", provider=provider, model=model)
            return None
        try:
            return decode_json(row[0])
        except (ValueError, RecursionError) as exc:
            _log.warning("opencode 2 price catalog entry is malformed", detail=str(exc))
            return None


def resolve_catalog_store_path(env: Mapping[str, str]) -> Path | None:
    """OpenCode 2's own store location, resolved from the worker's environment mapping: ``XDG_DATA_HOME`` when
    non-empty, otherwise ``HOME/.local/share``, then ``opencode/opencode.db``. ``None`` with neither set."""
    data_home = env.get("XDG_DATA_HOME")
    if data_home:
        return Path(data_home) / "opencode" / "opencode.db"
    home = env.get("HOME")
    if not home:
        return None
    return Path(home) / ".local" / "share" / "opencode" / "opencode.db"


def _number(value: JsonValue) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _parse_override(entry: JsonValue) -> _ContextOverride | None:
    """A lone-``context`` override; any other condition is skipped, and so is a malformed entry."""
    if not isinstance(entry, dict):
        return None
    when = entry.get("when")
    if not isinstance(when, dict) or set(when) != {"context"}:
        return None
    context = when["context"]
    if not isinstance(context, dict) or len(context) != 1:
        return None
    ((operator, bound),) = context.items()
    threshold = _number(bound)
    if operator not in ("gt", "gte") or threshold is None:
        return None
    cache = entry.get("cache")
    cache_map = cache if isinstance(cache, dict) else {}
    return _ContextOverride(
        threshold=threshold,
        inclusive=operator == "gte",
        price=_number(entry.get("price")),
        cache_read=_number(cache_map.get("read")),
        cache_write=_number(cache_map.get("write")),
    )


def _parse_ladder(entry: JsonValue) -> tuple[_RateLadder, bool] | None:
    """One class's ladder and whether its cache write is TTL-keyed; ``None`` when unpriceable: no price, or a rate
    naming a ``unit``."""
    if not isinstance(entry, dict) or "unit" in entry:
        return None
    price = _number(entry.get("price"))
    if price is None:
        return None
    overrides_raw = entry.get("overrides")
    overrides = (
        tuple(o for o in (_parse_override(item) for item in overrides_raw) if o is not None)
        if isinstance(overrides_raw, list)
        else ()
    )
    if any(
        isinstance(item, dict) and "unit" in item for item in (overrides_raw if isinstance(overrides_raw, list) else ())
    ):
        return None
    cache = entry.get("cache")
    cache_map = cache if isinstance(cache, dict) else {}
    write = cache_map.get("write")
    return (
        _RateLadder(
            price=price,
            cache_read=_number(cache_map.get("read")) or 0.0,
            cache_write=_number(write) or 0.0,
            overrides=overrides,
        ),
        isinstance(write, dict),
    )


def _parse_price(text: JsonValue) -> OpenCodeTwoModelPrice | None:
    if not isinstance(text, dict):
        return None
    parsed_input = _parse_ladder(text.get("input"))
    parsed_output = _parse_ladder(text.get("output"))
    if parsed_input is None or parsed_output is None:
        return None
    ladder, by_ttl = parsed_input
    return OpenCodeTwoModelPrice(input=ladder, output=parsed_output[0], cache_write_by_ttl=by_ttl)


# Typecheck-time Protocol conformance sentinel: pyright rejects the return if the reader drifts from the seam.
_catalog_conforms: type[IOpenCodeTwoPriceCatalog] = SqliteOpenCodeTwoPriceCatalog

__all__ = [
    "CATALOG_KEY",
    "IOpenCodeTwoPriceCatalog",
    "OpenCodeTwoModelPrice",
    "SqliteOpenCodeTwoPriceCatalog",
    "resolve_catalog_store_path",
]
