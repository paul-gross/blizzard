"""The OpenCode price-cache seam (``bzh:pluggable-seams``).

OpenCode's own ``models.json`` cache carries per-model dollar rates; this binding translates
a model id and one step's tokens into a dollar estimate under OpenCode's own tier rule.
Blizzard keeps no price table, and deciding when a step needs an estimate stays with the
caller of :class:`IOpenCodePriceCatalog`."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from blizzard.foundation.logging import get_logger

_log = get_logger("blizzard.runner.harness")

#: The only ``cost.tiers[].tier.type`` priced against; any other tier kind is skipped.
_CONTEXT_TIER_TYPE = "context"

#: The fixed threshold ``cost.context_over_200k`` applies at, independent of any tier's own size.
_CONTEXT_OVER_200K_THRESHOLD = 200_000

#: OpenCode's cost schema is dollars per 1,000,000 tokens.
_TOKENS_PER_RATE_UNIT = 1_000_000.0


@dataclass(frozen=True)
class OpenCodeRate:
    """One ``cost``-shaped rate: dollars per 1,000,000 tokens, by token class. A missing
    ``cache_read``/``cache_write`` in the source JSON defaults to 0 here, mirroring
    OpenCode's own schema default."""

    input: float
    output: float
    cache_read: float = 0.0
    cache_write: float = 0.0


@dataclass(frozen=True)
class OpenCodeContextTier:
    """One ``cost.tiers[]`` entry whose ``tier.type`` is ``"context"``: ``rate`` applies once
    a step's prompt size exceeds ``size`` tokens."""

    size: float
    rate: OpenCodeRate


@dataclass(frozen=True)
class OpenCodeStepTokens:
    """One step's token counts, split the way a priced rate is."""

    input: int
    output: int
    reasoning: int = 0
    cache_read: int = 0
    cache_write: int = 0


@dataclass(frozen=True)
class OpenCodeModelPrice:
    """One model's cache entry, priceable because its ``base`` rate carries both an
    ``input`` and an ``output`` rate — a ``cost`` entry missing either is not represented by
    one of these at all (see ``_parse_rate``); the catalog returns ``None`` instead."""

    base: OpenCodeRate
    context_over_200k: OpenCodeRate | None = None
    tiers: tuple[OpenCodeContextTier, ...] = ()

    def estimate(self, tokens: OpenCodeStepTokens) -> float:
        """This step's estimated cost in dollars, at the one rate OpenCode's own rule
        selects for the step's own prompt size — the whole step is priced at that rate, not
        just the tokens past a threshold. Reasoning tokens are priced at the output rate."""
        rate = self._rate_for(prompt_size=tokens.input + tokens.cache_read + tokens.cache_write)
        billed_output = tokens.output + tokens.reasoning
        return (
            tokens.input * rate.input
            + billed_output * rate.output
            + tokens.cache_read * rate.cache_read
            + tokens.cache_write * rate.cache_write
        ) / _TOKENS_PER_RATE_UNIT

    def _rate_for(self, *, prompt_size: float) -> OpenCodeRate:
        # The largest context tier the prompt size still clears — never one chosen from a
        # different step's tokens.
        cleared_tiers = [tier for tier in self.tiers if prompt_size > tier.size]
        if cleared_tiers:
            return max(cleared_tiers, key=lambda tier: tier.size).rate
        if self.context_over_200k is not None and prompt_size > _CONTEXT_OVER_200K_THRESHOLD:
            return self.context_over_200k
        return self.base


class IOpenCodePriceCatalog(Protocol):
    """The one-method price-lookup seam (``bzh:seam-size-ceiling``)."""

    def price_for(self, provider: str, model: str) -> OpenCodeModelPrice | None:
        """``provider``'s ``model``'s price, or ``None`` when the cache has no priceable
        entry for it: a missing cache file, unreadable or malformed JSON, an unknown
        provider or model, or a ``cost`` entry with no ``input`` or ``output`` rate. Never
        raises."""
        ...


class FileOpenCodePriceCatalog:
    """Reads OpenCode's own ``models.json`` at ``path``, keyed ``provider -> "models" ->
    model id -> "cost"``. Re-reads the file on every lookup, since OpenCode refreshes it
    independently; a caller looks a model up at most once per parsed invocation."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def price_for(self, provider: str, model: str) -> OpenCodeModelPrice | None:
        document = self._read_document()
        if document is None:
            return None
        provider_entry = document.get(provider)
        models = provider_entry.get("models") if isinstance(provider_entry, dict) else None
        model_entry = models.get(model) if isinstance(models, dict) else None
        if not isinstance(model_entry, dict):
            _log.warning(
                "opencode price cache has no entry for model", path=str(self._path), provider=provider, model=model
            )
            return None
        price = _parse_price(model_entry.get("cost"))
        if price is None:
            _log.warning(
                "opencode price cache entry is unpriceable", path=str(self._path), provider=provider, model=model
            )
        return price

    def _read_document(self) -> dict[str, object] | None:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            _log.warning("opencode price cache is unreadable", path=str(self._path), detail=str(exc))
            return None
        try:
            decoded = json.loads(raw)
        except (json.JSONDecodeError, RecursionError) as exc:
            _log.warning("opencode price cache is malformed", path=str(self._path), detail=str(exc))
            return None
        if not isinstance(decoded, dict):
            _log.warning("opencode price cache is not a JSON object", path=str(self._path))
            return None
        return decoded


def resolve_price_cache_path(env: Mapping[str, str]) -> Path | None:
    """OpenCode's own price-cache location, resolved from the worker's own environment
    mapping — never a constant. ``XDG_CACHE_HOME`` when the operator passed it through to the
    worker, otherwise ``HOME/.cache``, matching OpenCode's own cache-root resolution. ``None``
    with neither variable passed through: an unrooted relative path would read whatever sits
    under the worker's own cwd, so this reads as no cache rather than a wrong one."""
    xdg_cache_home = env.get("XDG_CACHE_HOME")
    if xdg_cache_home:
        return Path(xdg_cache_home) / "opencode" / "models.json"
    home = env.get("HOME")
    if not home:
        return None
    return Path(home) / ".cache" / "opencode" / "models.json"


def _parse_price(cost: object) -> OpenCodeModelPrice | None:
    if not isinstance(cost, dict):
        return None
    base = _parse_rate(cost)
    if base is None:
        return None
    over_200k_raw = cost.get("context_over_200k")
    context_over_200k = _parse_rate(over_200k_raw) if isinstance(over_200k_raw, dict) else None
    tiers_raw = cost.get("tiers")
    tiers = (
        tuple(
            tier for tier in (_parse_tier(entry) for entry in tiers_raw if isinstance(entry, dict)) if tier is not None
        )
        if isinstance(tiers_raw, list)
        else ()
    )
    return OpenCodeModelPrice(base=base, context_over_200k=context_over_200k, tiers=tiers)


def _parse_rate(rate: Mapping[str, object]) -> OpenCodeRate | None:
    input_rate = _parse_number(rate.get("input"))
    output_rate = _parse_number(rate.get("output"))
    if input_rate is None or output_rate is None:
        return None
    return OpenCodeRate(
        input=input_rate,
        output=output_rate,
        cache_read=_parse_number(rate.get("cache_read")) or 0.0,
        cache_write=_parse_number(rate.get("cache_write")) or 0.0,
    )


def _parse_tier(entry: Mapping[str, object]) -> OpenCodeContextTier | None:
    tier = entry.get("tier")
    if not isinstance(tier, dict) or tier.get("type") != _CONTEXT_TIER_TYPE:
        return None
    size = _parse_number(tier.get("size"))
    rate = _parse_rate(entry)
    if size is None or rate is None:
        return None
    return OpenCodeContextTier(size=size, rate=rate)


def _parse_number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


# Typecheck-time Protocol conformance sentinel (the exemplar's shape): pyright rejects the
# return if `FileOpenCodePriceCatalog` drifts from `IOpenCodePriceCatalog`.
def _conforms_catalog(x: FileOpenCodePriceCatalog) -> IOpenCodePriceCatalog:
    return x
