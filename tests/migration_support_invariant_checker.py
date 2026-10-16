"""Helpers the ``invariant_checker`` tests share across the revisions they prove."""

from __future__ import annotations

from datetime import UTC, datetime

import sqlalchemy as sa

from tests.runner_fakes import FIXTURE_RUNNER_ID

_NOW = datetime(2026, 7, 14, tzinfo=UTC)


def insert(table: sa.Table) -> sa.Insert:
    """An insert that names the fixture runner on runner-scoped tables, as the statement guard requires."""
    statement = sa.insert(table)
    if table.info.get("scope") == "runner":
        return statement.values(runner_id=FIXTURE_RUNNER_ID)
    return statement


_PROJECT_INVARIANTS = frozenset(
    {
        "hub:chunk-project-in-tenant",
        "hub:routine-scope-same-project",
        "hub:finding-scope-in-project",
        "hub:review-finding-project-is-chunks",
        "hub:question-project-is-chunks",
        "hub:decision-project-is-chunks",
        "hub:proposal-cites-own-project",
        "hub:work-item-project-in-tenant",
        "hub:slug-not-current-and-former",
        "hub:work-source-references-in-tenant",
        "hub:chunk-sources-linked",
        "hub:landed-repository-linked",
    }
)
