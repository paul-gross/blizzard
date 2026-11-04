"""Helpers for the baseline floor tests, shared with other test modules."""

from __future__ import annotations

from pathlib import Path

from blizzard.runner.store import MIGRATIONS_DIR


def make_tree(tmp_path: Path) -> Path:
    scripts = tmp_path / "scripts"
    versions = scripts / "versions"
    versions.mkdir(parents=True)
    (scripts / "env.py").write_text((MIGRATIONS_DIR / "env.py").read_text())
    (versions / "base.py").write_text(
        'revision = "base_rev"\ndown_revision = None\n'
        'BASELINE_FLOOR = "v0.2.0"\nABSORBED = frozenset({"old_rev"})\n'
        "def upgrade(): pass\ndef downgrade(): pass\n"
    )
    (versions / "next.py").write_text(
        'revision = "next_rev"\ndown_revision = "base_rev"\ndef upgrade(): pass\ndef downgrade(): pass\n'
    )
    return scripts
