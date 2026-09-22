"""``harness/internal/opencode_price_cache.py`` — the OpenCode price-cache seam (unit).

Mirrors ``test_runner_harness_opencode_export.py``'s own shape: the value object and its tier
rule are tested directly (no file I/O), and the file-backed catalog is tested against
``tmp_path`` fixtures shaped like a real ``models.json``, never a real OpenCode cache."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from blizzard.runner.harness.internal.opencode_price_cache import (
    FileOpenCodePriceCatalog,
    OpenCodeContextTier,
    OpenCodeModelPrice,
    OpenCodeRate,
    OpenCodeStepTokens,
    resolve_price_cache_path,
)

pytestmark = pytest.mark.unit

# `gpt-5.6-luna`'s base rates, dollars per 1,000,000 tokens (the issue's worked example).
_LUNA_BASE = OpenCodeRate(input=0.20, output=1.20, cache_read=0.02, cache_write=0.25)
_LUNA_TIER = OpenCodeContextTier(
    size=272_000, rate=OpenCodeRate(input=0.40, output=1.80, cache_read=0.04, cache_write=0.50)
)


# --- OpenCodeModelPrice.estimate — the tier rule -----------------------------------------


def test_estimate_uses_base_rates_under_a_roughly_40k_prompt_with_no_tier() -> None:
    price = OpenCodeModelPrice(base=_LUNA_BASE, tiers=(_LUNA_TIER,))
    tokens = OpenCodeStepTokens(input=40_000, output=2_000, cache_read=1_000, cache_write=500)

    # F = 40_000 + 1_000 + 500 = 41_500, well under the 272_000 tier — base rates apply.
    expected = (40_000 * 0.20 + 2_000 * 1.20 + 1_000 * 0.02 + 500 * 0.25) / 1_000_000
    assert price.estimate(tokens) == pytest.approx(expected)


def test_estimate_prices_every_token_class_at_the_tier_rate_once_the_prompt_exceeds_tier_size() -> None:
    price = OpenCodeModelPrice(base=_LUNA_BASE, tiers=(_LUNA_TIER,))
    tokens = OpenCodeStepTokens(input=280_000, output=2_000, cache_read=1_000, cache_write=500)

    # F = 281_500 > 272_000 — the whole step, every token class, prices at the tier rate.
    expected = (280_000 * 0.40 + 2_000 * 1.80 + 1_000 * 0.04 + 500 * 0.50) / 1_000_000
    assert price.estimate(tokens) == pytest.approx(expected)


def test_estimate_uses_context_over_200k_between_200k_and_the_tier_size() -> None:
    over_200k = OpenCodeRate(input=0.35, output=1.75, cache_read=0.035, cache_write=0.40)
    price = OpenCodeModelPrice(base=_LUNA_BASE, context_over_200k=over_200k, tiers=(_LUNA_TIER,))
    tokens = OpenCodeStepTokens(input=210_000, output=1_000)

    # F = 210_000: over the 200k threshold but not over the 272_000 tier, so the tier is
    # never cleared and `context_over_200k` is used instead of the base rate.
    expected = (210_000 * 0.35 + 1_000 * 1.75) / 1_000_000
    assert price.estimate(tokens) == pytest.approx(expected)


def test_estimate_bills_reasoning_tokens_at_the_output_rate() -> None:
    price = OpenCodeModelPrice(base=OpenCodeRate(input=0.20, output=1.20))
    tokens = OpenCodeStepTokens(input=1_000, output=0, reasoning=500)

    expected = (1_000 * 0.20 + 500 * 1.20) / 1_000_000
    assert price.estimate(tokens) == pytest.approx(expected)


def test_estimate_prices_two_steps_on_either_side_of_a_threshold_at_their_own_rate() -> None:
    price = OpenCodeModelPrice(base=_LUNA_BASE, tiers=(_LUNA_TIER,))
    below = OpenCodeStepTokens(input=40_000, output=1_000)
    above = OpenCodeStepTokens(input=280_000, output=1_000)

    below_expected = (40_000 * 0.20 + 1_000 * 1.20) / 1_000_000
    above_expected = (280_000 * 0.40 + 1_000 * 1.80) / 1_000_000
    assert price.estimate(below) == pytest.approx(below_expected)
    assert price.estimate(above) == pytest.approx(above_expected)
    assert price.estimate(below) != price.estimate(above)


def test_estimate_prices_a_missing_cache_rate_at_zero() -> None:
    price = OpenCodeModelPrice(base=OpenCodeRate(input=0.20, output=1.20, cache_read=0.02))  # no cache_write
    tokens = OpenCodeStepTokens(input=1_000, output=100, cache_read=200, cache_write=5_000)

    expected = (1_000 * 0.20 + 100 * 1.20 + 200 * 0.02) / 1_000_000  # cache_write contributes 0
    assert price.estimate(tokens) == pytest.approx(expected)


# --- FileOpenCodePriceCatalog — file-backed lookup ----------------------------------------


def _write_cache(path: Path, document: dict[str, object]) -> None:
    path.write_text(json.dumps(document), encoding="utf-8")


def _luna_cost(**overrides: object) -> dict[str, object]:
    cost: dict[str, object] = {"input": 0.20, "output": 1.20, "cache_read": 0.02, "cache_write": 0.25}
    cost.update(overrides)
    return cost


def test_catalog_looks_up_a_price_by_provider_and_model(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    _write_cache(
        path,
        {
            "openai": {
                "models": {
                    "gpt-5.6-luna": {
                        "cost": {
                            **_luna_cost(),
                            "tiers": [
                                {
                                    "input": 0.40,
                                    "output": 1.80,
                                    "cache_read": 0.04,
                                    "cache_write": 0.50,
                                    "tier": {"type": "context", "size": 272_000},
                                }
                            ],
                            "context_over_200k": {"input": 0.40, "output": 1.80, "cache_read": 0.04},
                        }
                    }
                }
            }
        },
    )
    catalog = FileOpenCodePriceCatalog(path)

    price = catalog.price_for("openai", "gpt-5.6-luna")

    assert price is not None
    assert price.base == _LUNA_BASE
    assert price.tiers == (OpenCodeContextTier(size=272_000, rate=OpenCodeRate(0.40, 1.80, 0.04, 0.50)),)
    assert price.context_over_200k == OpenCodeRate(input=0.40, output=1.80, cache_read=0.04)


def test_catalog_ignores_a_non_context_tier_entry(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    _write_cache(
        path,
        {
            "openai": {
                "models": {
                    "gpt-5.6-luna": {
                        "cost": {
                            **_luna_cost(),
                            "tiers": [
                                {
                                    "input": 0.40,
                                    "output": 1.80,
                                    "cache_read": 0.04,
                                    "cache_write": 0.50,
                                    "tier": {"type": "usage", "size": 1_000},
                                }
                            ],
                        }
                    }
                }
            }
        },
    )
    catalog = FileOpenCodePriceCatalog(path)

    price = catalog.price_for("openai", "gpt-5.6-luna")

    assert price is not None
    assert price.tiers == ()
    # F = 2_000 clears the ignored entry's 1_000 size — still prices at the base rate.
    tokens = OpenCodeStepTokens(input=2_000, output=100)
    expected = (2_000 * 0.20 + 100 * 1.20) / 1_000_000
    assert price.estimate(tokens) == pytest.approx(expected)


def test_catalog_prices_the_same_model_id_differently_under_two_providers(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    _write_cache(
        path,
        {
            "openai": {"models": {"gpt-5.6-luna": {"cost": _luna_cost()}}},
            "azure": {"models": {"gpt-5.6-luna": {"cost": _luna_cost(input=1.0, output=6.0)}}},
        },
    )
    catalog = FileOpenCodePriceCatalog(path)

    openai_price = catalog.price_for("openai", "gpt-5.6-luna")
    azure_price = catalog.price_for("azure", "gpt-5.6-luna")

    assert openai_price is not None
    assert azure_price is not None
    assert openai_price.base.input == 0.20
    assert azure_price.base.input == 1.0
    assert openai_price != azure_price


def test_catalog_missing_file_returns_none_without_raising(tmp_path: Path) -> None:
    catalog = FileOpenCodePriceCatalog(tmp_path / "does-not-exist.json")

    assert catalog.price_for("openai", "gpt-5.6-luna") is None


def test_catalog_malformed_json_returns_none_without_raising(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    path.write_text("{not valid json", encoding="utf-8")
    catalog = FileOpenCodePriceCatalog(path)

    assert catalog.price_for("openai", "gpt-5.6-luna") is None


def test_catalog_non_utf8_bytes_return_none_without_raising(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    path.write_bytes(b"\xff\xfe\x00not utf-8")

    assert FileOpenCodePriceCatalog(path).price_for("openai", "gpt-5.6-luna") is None


def test_catalog_json_nested_past_the_recursion_limit_returns_none_without_raising(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    path.write_text("[" * 200_000, encoding="utf-8")

    assert FileOpenCodePriceCatalog(path).price_for("openai", "gpt-5.6-luna") is None


def test_catalog_unknown_provider_or_model_returns_none(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    _write_cache(path, {"openai": {"models": {"gpt-5.6-luna": {"cost": _luna_cost()}}}})
    catalog = FileOpenCodePriceCatalog(path)

    assert catalog.price_for("unknown-provider", "gpt-5.6-luna") is None
    assert catalog.price_for("openai", "unknown-model") is None


def test_catalog_missing_input_or_output_rate_is_unpriceable(tmp_path: Path) -> None:
    path = tmp_path / "models.json"
    _write_cache(
        path,
        {
            "openai": {
                "models": {
                    "no-input": {"cost": {"output": 1.20}},
                    "no-output": {"cost": {"input": 0.20}},
                }
            }
        },
    )
    catalog = FileOpenCodePriceCatalog(path)

    assert catalog.price_for("openai", "no-input") is None
    assert catalog.price_for("openai", "no-output") is None


# --- resolve_price_cache_path ---------------------------------------------------------------


def test_resolve_price_cache_path_uses_xdg_cache_home_when_passed_through() -> None:
    env = {"HOME": "/home/operator", "XDG_CACHE_HOME": "/custom/cache"}

    assert resolve_price_cache_path(env) == Path("/custom/cache/opencode/models.json")


def test_resolve_price_cache_path_falls_back_to_home_cache_dir() -> None:
    env = {"HOME": "/home/operator"}

    assert resolve_price_cache_path(env) == Path("/home/operator/.cache/opencode/models.json")


def test_resolve_price_cache_path_is_none_with_neither_variable_passed_through() -> None:
    assert resolve_price_cache_path({}) is None
