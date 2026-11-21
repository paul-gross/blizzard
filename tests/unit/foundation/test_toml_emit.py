"""The settings document emitter: what it prints decodes back to the document it was given."""

from __future__ import annotations

import pytest

from blizzard.foundation.toml_emit import toml_document
from blizzard.runner.host_config import decode_host_text


@pytest.mark.parametrize(
    "document",
    [
        {},
        {"tick_seconds": 5, "ratio": 0.5, "on": True, "name": '☃️ "quoted"'},
        {"tracing": {"batch_limit": 10, "worker_program_services": {"claude": "svc", "a.b": "c"}}},
        {"tools": {"path_prepend": ["/a", "/b"], "inline": [{"x": 1}, {"y": [1, 2]}], "empty": []}},
        {
            "tick_seconds": 3,
            "runner": [
                {"id": "rn_1", "agents": 1, "harness": {"autonomy": "full"}, "projects": [{"project": "p"}]},
                {"id": "rn_2", "models": {"aliases": dict[str, object]()}},
            ],
            "workspace": [{"id": "ws_1", "repo": [{"name": "a", "url": "u"}], "envs": ["e1"]}],
        },
    ],
)
def test_the_emitted_document_decodes_to_its_input(document: dict[str, object]) -> None:
    assert decode_host_text(toml_document(document), what="emitted") == document


def test_tables_follow_their_keys_and_arrays_of_tables_are_sections() -> None:
    text = toml_document({"runner": [{"id": "rn_1", "harness": {"autonomy": "full"}}], "tick_seconds": 3})
    assert text == '"tick_seconds" = 3\n\n[["runner"]]\n"id" = "rn_1"\n\n["runner"."harness"]\n"autonomy" = "full"\n'
