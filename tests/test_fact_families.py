"""The fact-family registry (``bzh:fact-families-declared``): every chunk-keyed table is declared, and every
projection reads exactly what the registry says it carries."""

from __future__ import annotations

import pytest

from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_events_store, chunk_facts_store, corpus_rows
from blizzard.hub.store.internal.fact_families import EXCLUDED, FACT_TABLES, Projection, families, tables

pytestmark = pytest.mark.unit


def _chunk_keyed() -> set[str]:
    keyed: set[str] = set()
    for table in s.metadata.sorted_tables:
        if table.name == "chunks":
            continue
        for column in table.columns:
            references_chunks = any(
                fk.column.table.name == "chunks" and fk.column.name == "chunk_id" for fk in column.foreign_keys
            )
            if references_chunks or column.name == "chunk_id" or column.name.endswith("_chunk_id"):
                keyed.add(table.name)
    return keyed


def test_every_chunk_keyed_table_is_registered_or_excluded_never_both() -> None:
    registered = {e.table.name for e in FACT_TABLES}
    excluded = set(EXCLUDED)
    assert not registered & excluded, "a table is registered or excluded, never both"
    undeclared = {name for name in _chunk_keyed() if name not in registered | excluded}
    assert not undeclared, f"chunk-keyed tables neither registered nor excluded: {sorted(undeclared)}"


def test_every_registered_or_excluded_name_is_a_real_table() -> None:
    known = set(s.metadata.tables)
    assert {e.table.name for e in FACT_TABLES} <= known
    assert set(EXCLUDED) <= known


def test_each_table_is_registered_once() -> None:
    names = [e.table.name for e in FACT_TABLES]
    assert len(names) == len(set(names))


def test_the_facts_load_has_a_mapper_for_each_family_the_registry_carries() -> None:
    assert chunk_facts_store.LOADED_FAMILIES == families(Projection.FACTS) == chunk_facts_store.ALL_FAMILIES


def test_the_status_families_are_among_the_loaded_ones() -> None:
    assert chunk_facts_store.STATUS_FAMILIES <= chunk_facts_store.ALL_FAMILIES
    assert chunk_facts_store.TICK_STATUS_FAMILIES <= chunk_facts_store.ALL_FAMILIES
    assert chunk_facts_store.MOVEMENT_FAMILIES <= chunk_facts_store.ALL_FAMILIES


def test_the_trace_reads_the_tables_the_registry_says() -> None:
    assert set(corpus_rows.TRACE_READERS) == set(tables(Projection.TRACE))


def test_the_event_feed_reads_the_tables_the_registry_says() -> None:
    assert set(chunk_events_store.EVENT_SOURCES) == set(tables(Projection.EVENTS))


def test_closing_candidates_page_by_a_time_and_an_id() -> None:
    assert all(e.fact_time is not None for e in FACT_TABLES if Projection.CLOSING in e.projections)
