"""Write one baseline revision: the floor release's schema as it declares it, and the rows its history seeds."""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping, Sequence
from datetime import datetime
from textwrap import indent
from typing import Any

from blizzard.foundation.untyped import as_table
from blizzard.tools.store_fold.seeds import classify

_PRELUDE = '''\
def _encode(value: int) -> str:
    chars = []
    for _ in range(_ULID_CHARS):
        value, rem = divmod(value, 32)
        chars.append(_CROCKFORD[rem])
    return "".join(reversed(chars))


def _mint(prefix: str, at: datetime) -> str:
    millis = int(at.timestamp() * 1000)
    randomness = int.from_bytes(os.urandom(_RANDOM_BITS // 8), "big")
    return f"{prefix}_{_encode((millis << _RANDOM_BITS) | randomness)}"


def _regenerate(text: str, minted: dict[str, str], at: datetime) -> str:
    """``text`` with each id it holds replaced by a fresh one, the same fresh one wherever the same id recurs."""
    return _MINTED_ID.sub(lambda found: minted.setdefault(found.group(0), _mint(found.group(1), at)), text)
'''

_PRELUDE_CONSTANTS = """\
# Restated, not imported, from ``blizzard.foundation.ids`` (``bzh:frozen-revisions``).
_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"
_ULID_CHARS = 26  # 48 bits of millisecond timestamp, then 80 random bits
_RANDOM_BITS = 80
_MINTED_ID = re.compile(r"(?<![A-Za-z0-9])([a-z]{2,6})_[0-9A-HJKMNP-TV-Z]{26}(?![A-Za-z0-9])")
"""


def _literal(value: Any) -> str:
    """``value`` as the source of a seeded value, regenerating a minted id or an instant at upgrade time."""
    match classify(value):
        case "timestamp":
            return "now"
        case "minted":
            return f"_regenerate({value!r}, minted, now)"
        case "literal":
            return repr(value)


def _column(name: str, values: Sequence[Any]) -> str:
    if any(isinstance(value, datetime) for value in values):
        return f"sa.column({name!r}, UtcDateTime())"
    if any(isinstance(value, bool) for value in values):
        return f"sa.column({name!r}, sa.Boolean())"
    return f"sa.column({name!r})"


def decode(value: object) -> object:
    """A value as the floor-side renderer transported it."""
    table = as_table(value)
    if table is not None and "$datetime" in table:
        return datetime.fromisoformat(str(table["$datetime"]))
    if table is not None and "$bytes" in table:
        return base64.b64decode(str(table["$bytes"]))
    return value


def _seed_block(tables: Mapping[str, Sequence[Mapping[str, Any]]], sequences: Mapping[str, str]) -> tuple[str, str]:
    """The module-level seed tables and the ``upgrade`` lines that insert them."""
    definitions: list[str] = []
    inserts = ["now = migration_clock().now()", "minted: dict[str, str] = {}"]
    for table, rows in tables.items():
        columns = ", ".join(_column(name, [row[name] for row in rows]) for name in rows[0])
        definitions.append(f"_seed_{table} = sa.table({table!r}, {columns})")
        values = ",\n".join(
            "    {" + ", ".join(f"{name!r}: {_literal(value)}" for name, value in row.items()) + "}" for row in rows
        )
        inserts.append(f"op.bulk_insert(_seed_{table}, [\n{values},\n])")
        if table in sequences:
            key = sequences[table]
            inserts.append(
                'if op.get_bind().dialect.name == "postgresql":\n'
                "    # An explicit key leaves the sequence behind it; the next insert would collide.\n"
                f"    op.execute(sa.text(\"SELECT setval(pg_get_serial_sequence('{table}', '{key}'), "
                f'(SELECT max({key}) FROM {table}))"))'
            )
    return "\n".join(definitions), "\n".join(inserts)


def write_baseline(
    *,
    tree: str,
    revision: str,
    floor_tag: str,
    absorbed: frozenset[str],
    rendered: Mapping[str, Any],
) -> str:
    """The source of the baseline revision ``revision`` for ``tree``, standing for everything ``absorbed`` named."""
    tables = {
        table: [{name: decode(value) for name, value in row.items()} for row in rows]
        for table, rows in rendered["tables"].items()
    }
    sequences: Mapping[str, str] = rendered["sequences"]
    seeded = bool(tables)
    imports = ["from __future__ import annotations", ""]
    if seeded:
        imports += ["import os", "import re"]
    imports += ["from collections.abc import Sequence"]
    if seeded:
        imports += ["from datetime import datetime"]
    imports += ["", "import sqlalchemy as sa", "from alembic import op", ""]
    if seeded:
        imports += ["from blizzard.foundation.store.migrations import migration_clock"]
    imports += ["from blizzard.foundation.store.utc import UtcDateTime"]

    absorbed_source = "\n".join(f'    "{item}",' for item in sorted(absorbed))
    parts = [
        f'"""{tree} store baseline — the history up to {floor_tag} as one revision.\n\n'
        f"Creates the schema {floor_tag} declares, and inserts the rows its history seeds. An id minted by a seeded "
        "row "
        "and the instant a seeded row is stamped with are minted and stamped afresh when this runs, each id once "
        "wherever it recurs; every other value is the one the history wrote. A store at a revision in ``ABSORBED`` is "
        f'refused, naming ``BASELINE_FLOOR``.\n\nRevision ID: {revision}\nRevises:\n"""\n',
        "\n".join(imports),
        f'\nrevision: str = "{revision}"\ndown_revision: str | None = None\n'
        "branch_labels: str | Sequence[str] | None = None\ndepends_on: str | Sequence[str] | None = None\n",
        f'BASELINE_FLOOR = "{floor_tag}"\n'
        f"ABSORBED = frozenset(\n    {{\n{indent(absorbed_source, '    ')}\n    }}\n)\n",
    ]
    seed_inserts = ""
    if seeded:
        definitions, seed_inserts = _seed_block(tables, sequences)
        parts += [_PRELUDE_CONSTANTS, definitions + "\n", "\n" + _PRELUDE]
    parts.append(
        "\ndef upgrade() -> None:\n"
        + _body(rendered["upgrade"])
        + (("\n" + indent(seed_inserts, "    ") + "\n") if seeded else "")
    )
    parts.append("\n\ndef downgrade() -> None:\n" + _body(rendered["downgrade"]))
    return re.sub(r"\n{4,}", "\n\n\n", "\n".join(parts)).rstrip("\n") + "\n"


def _body(code: str) -> str:
    return code.rstrip() + "\n" if code.strip() else "    pass\n"
