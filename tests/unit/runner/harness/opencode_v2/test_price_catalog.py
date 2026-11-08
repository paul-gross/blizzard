"""The OpenCode 2 price catalog: the model's override rule, the store reader, and the store's location."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from blizzard.runner.harness.opencode_v2.price_catalog import (
    CATALOG_KEY,
    SqliteOpenCodeTwoPriceCatalog,
    resolve_catalog_store_path,
)
from blizzard.runner.harness.opencode_v2.shapes import Tokens

FIXTURE = (
    Path(__file__).resolve().parents[5] / "src/blizzard/runner/harness/contracts/opencode/2.2.6/models_catalog_row.json"
)


def seed_store(path: Path, *, body: object | None = None, key: str = CATALOG_KEY, raw_value: str | None = None) -> Path:
    """An OpenCode 2 store at ``path`` holding the fixture's catalog row (or ``body`` in its place)."""
    row = json.loads(FIXTURE.read_text())
    if body is not None:
        row["body"] = json.dumps(body)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute("create table kv (key text primary key, value text, time_created int, time_updated int)")
    connection.execute("insert into kv values (?, ?, 0, 0)", (key, json.dumps(row) if raw_value is None else raw_value))
    connection.commit()
    connection.close()
    return path


@pytest.fixture
def catalog(tmp_path: Path) -> SqliteOpenCodeTwoPriceCatalog:
    return SqliteOpenCodeTwoPriceCatalog(seed_store(tmp_path / "opencode.db"))


def test_a_step_under_every_override_is_priced_at_the_base_rate(catalog: SqliteOpenCodeTwoPriceCatalog) -> None:
    price = catalog.price_for("openai", "gpt-6.1-sol")
    assert price is not None
    # 1000 in at 2, 100 out at 10, 500 cache read at 0.1, 200 cache write at 2.5 — per million tokens
    assert price.estimate(Tokens(input=1000, output=100, cache_read=500, cache_write=200)) == pytest.approx(0.00355)


def test_a_step_above_the_override_is_priced_at_it_throughout(catalog: SqliteOpenCodeTwoPriceCatalog) -> None:
    price = catalog.price_for("openai", "gpt-6.1-sol")
    assert price is not None
    over = Tokens(input=200_000, output=1000, reasoning=500, cache_read=100_000, cache_write=1_000)
    # prompt size 301000 > 272000: 4 / 15 / 0.2 / 5, reasoning billed as output
    assert price.estimate(over) == pytest.approx((200_000 * 4 + 1500 * 15 + 100_000 * 0.2 + 1000 * 5) / 1e6)


def test_the_prompt_size_leaves_output_out(catalog: SqliteOpenCodeTwoPriceCatalog) -> None:
    price = catalog.price_for("openai", "gpt-6.1-sol")
    assert price is not None
    at_threshold = Tokens(input=272_000, output=50_000)
    assert price.estimate(at_threshold) == pytest.approx((272_000 * 2 + 50_000 * 10) / 1e6)


def test_a_gte_override_applies_at_its_threshold(catalog: SqliteOpenCodeTwoPriceCatalog) -> None:
    price = catalog.price_for("xai", "grok-4.5")
    assert price is not None
    assert price.estimate(Tokens(input=199_999)) == pytest.approx(199_999 * 2 / 1e6)
    assert price.estimate(Tokens(input=200_000)) == pytest.approx(200_000 * 4 / 1e6)


def test_the_largest_threshold_that_applies_wins(catalog: SqliteOpenCodeTwoPriceCatalog) -> None:
    price = catalog.price_for("qiniu-ai", "qwen3-coder-480b-a35b-instruct")
    assert price is not None
    assert price.estimate(Tokens(input=130_000)) == pytest.approx(130_000 * 2.17391304 / 1e6)
    assert price.estimate(Tokens(input=40_000)) == pytest.approx(40_000 * 1.30434783 / 1e6)


def test_an_override_on_another_condition_is_skipped(catalog: SqliteOpenCodeTwoPriceCatalog) -> None:
    price = catalog.price_for("amazon-bedrock", "mistral.mistral-large-3-675b-instruct")
    assert price is not None
    assert price.estimate(Tokens(input=1000, output=1000)) == pytest.approx((1000 * 0.5 + 1000 * 1.5) / 1e6)


def test_a_ttl_keyed_cache_write_prices_only_a_step_that_wrote_none(catalog: SqliteOpenCodeTwoPriceCatalog) -> None:
    price = catalog.price_for("opper", "claude-sonnet-5")
    assert price is not None
    assert price.estimate(Tokens(input=1000, output=100, cache_read=1000)) == pytest.approx(
        (1000 * 2.2 + 100 * 11 + 1000 * 0.22) / 1e6
    )
    assert price.estimate(Tokens(input=1000, cache_write=1)) is None


@pytest.mark.parametrize(
    "cost",
    [
        {"text": {"input": {"price": 1}}},
        {"text": {"output": {"price": 1}}},
        {"text": {"input": {"price": 1, "unit": "x"}, "output": {"price": 1}}},
        {"text": {"input": {"price": 1}, "output": {"price": 1, "unit": 5}}},
        {"text": []},
        {},
    ],
)
def test_a_model_without_both_rates_or_naming_a_unit_has_no_price(tmp_path: Path, cost: object) -> None:
    store = seed_store(tmp_path / "opencode.db", body={"p": {"models": {"m": {"cost": cost}}}})
    assert SqliteOpenCodeTwoPriceCatalog(store).price_for("p", "m") is None


def test_an_unknown_model_or_provider_has_no_price(catalog: SqliteOpenCodeTwoPriceCatalog) -> None:
    assert catalog.price_for("openai", "nope") is None
    assert catalog.price_for("nope", "gpt-6.1-sol") is None
    assert catalog.price_for('open"ai', "gpt-6.1-sol") is None


def test_a_model_id_with_dots_is_looked_up_whole(tmp_path: Path) -> None:
    body = {"p": {"models": {"a.b": {"cost": {"text": {"input": {"price": 1}, "output": {"price": 2}}}}}}}
    store = seed_store(tmp_path / "opencode.db", body=body)
    assert SqliteOpenCodeTwoPriceCatalog(store).price_for("p", "a.b") is not None
    assert SqliteOpenCodeTwoPriceCatalog(store).price_for("p", "a") is None


def test_a_missing_store_row_or_bad_value_reads_as_no_price(tmp_path: Path) -> None:
    assert SqliteOpenCodeTwoPriceCatalog(tmp_path / "absent.db").price_for("openai", "gpt-6.1-sol") is None
    other_key = seed_store(tmp_path / "other.db", key="models-dev:v2:catalog:abc")
    assert SqliteOpenCodeTwoPriceCatalog(other_key).price_for("openai", "gpt-6.1-sol") is None
    not_json = seed_store(tmp_path / "bad.db", raw_value="{not json")
    assert SqliteOpenCodeTwoPriceCatalog(not_json).price_for("openai", "gpt-6.1-sol") is None
    garbage = tmp_path / "garbage.db"
    garbage.write_bytes(b"this is not a database" * 100)
    assert SqliteOpenCodeTwoPriceCatalog(garbage).price_for("openai", "gpt-6.1-sol") is None


def test_the_store_is_opened_read_only(tmp_path: Path) -> None:
    store = seed_store(tmp_path / "opencode.db")
    before = store.read_bytes()
    SqliteOpenCodeTwoPriceCatalog(store).price_for("openai", "gpt-6.1-sol")
    assert store.read_bytes() == before
    assert not list(tmp_path.glob("opencode.db-*"))


def test_the_store_resolves_from_xdg_data_home_else_home_else_nowhere() -> None:
    assert resolve_catalog_store_path({"XDG_DATA_HOME": "/x", "HOME": "/h"}) == Path("/x/opencode/opencode.db")
    assert resolve_catalog_store_path({"XDG_DATA_HOME": "", "HOME": "/h"}) == Path(
        "/h/.local/share/opencode/opencode.db"
    )
    assert resolve_catalog_store_path({"HOME": "/h"}) == Path("/h/.local/share/opencode/opencode.db")
    assert resolve_catalog_store_path({}) is None
