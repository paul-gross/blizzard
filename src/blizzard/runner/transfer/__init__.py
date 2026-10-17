"""The runner-transfer domain — one runner moved between two stores, and the rules an ingest of one is held to.

Owns the row-move seams (:mod:`.rows`), the runner-bound store port an ingest reads and writes through
(:mod:`.runner_stores`), the ingest refusals (:mod:`.refusals`), and the pure ingest rules (:mod:`.rules`)."""

from __future__ import annotations
