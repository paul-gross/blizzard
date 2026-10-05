"""The runnable SQL recipes of ``docs/deployment/egress.md`` ``### DuckDB``, found by their markers."""

from __future__ import annotations

import re

from tests.repo_files import repo_root

_DOC = repo_root() / "docs" / "deployment" / "egress.md"
COST_BY_NODE_BY_DAY = "cost-by-node-by-day"
SLOWEST_STATION_OF_THE_WEEK = "slowest-station-of-the-week"
FILES_A_STATION_READ_LAST_WEEK = "files-a-station-read-last-week"
SKILLS_BY_STATION = "skills-by-station"


def recipe(name: str) -> str:
    """The SQL of a recipe under ``### DuckDB``. It reads the dictionary's newest-copy view as ``steps_newest``, or ``events_current`` for an events recipe."""
    section = _DOC.read_text().split("### DuckDB", 1)[1].split("\n### ", 1)[0]
    match = re.search(rf"<!-- recipe:{re.escape(name)} -->\n\n```sql\n(.*?)\n```", section, re.DOTALL)
    assert match, f"no recipe {name!r} under ### DuckDB"
    return match.group(1)
