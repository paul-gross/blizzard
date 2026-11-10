"""``blizzard hub urgency`` and ``blizzard hub settings`` against a real hub: the set is listed in order with the
default marked; create places an urgency at the end, first, or after another; retire without ``--move-to``, on an
urgency open work carries, prints the count and the command to run, and with it moves the work; enable, order and
position reorder the set; and settings names and changes the default."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from click.testing import Result

from blizzard.hub.cli import hub as hub_group
from tests.support.hub_request_auth import ingest_chunk
from tests.support.scaffolding import HubHarness, build_hub
from tests.support.worker_http import Bound, bind_stubs

_HUB_URL = "http://testserver"

_bound: dict[str, Bound] = {}


@pytest.fixture
def hub(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path)
    forward_to = hub.client

    def forward(method: str, url: str, *, timeout: float, headers: dict[str, str] | None = None, **kwargs: Any) -> Any:
        return forward_to.request(method, url, headers=headers, **kwargs)

    _bound["hub"] = bind_stubs(request=forward)
    return hub


def _cli(*argv: str) -> Result:
    return _bound["hub"].runner.invoke(hub_group, [*argv, "--hub-url", _HUB_URL])


def _said(result: Result) -> list[str]:
    return [line for line in result.output.splitlines() if '"event": ' not in line]


def _json(result: Result) -> Any:
    assert result.exit_code == 0, result.output
    return json.loads(_said(result)[-1])


def _order() -> list[str]:
    return [row["slug"] for row in _json(_cli("urgency", "list", "--json"))["urgencies"]]


def test_list_shows_standard_alone_as_the_default_and_settings_names_it(hub: HubHarness) -> None:
    listed = _said(_cli("urgency", "list"))

    assert len(listed) == 1
    assert listed[0].startswith("1.  standard  name=standard")
    assert listed[0].endswith("default")
    assert _said(_cli("settings", "get"))[0] == "default urgency standard"


def test_create_places_it_at_the_end_first_or_after_another(hub: HubHarness) -> None:
    assert _cli("urgency", "create", "background").exit_code == 0
    assert _cli("urgency", "create", "urgent", "--emoji", "🔥", "--first").exit_code == 0
    assert _cli("urgency", "create", "soon", "--after", "urgent", "--name", "Soon").exit_code == 0

    assert _order() == ["urgent", "soon", "standard", "background"]
    shown = _said(_cli("urgency", "get", "urgent"))
    assert shown[3] == "mark 🔥"
    assert shown[4] == "place 1."


def test_create_refuses_a_bad_emoji_naming_the_field(hub: HubHarness) -> None:
    refused = _cli("urgency", "create", "bad", "--emoji", "ab")

    assert refused.exit_code != 0
    assert "emoji" in refused.output


def test_retire_without_move_to_prints_the_count_and_the_command_then_moves_the_work(hub: HubHarness) -> None:
    assert _cli("urgency", "create", "urgent").exit_code == 0
    assert _cli("settings", "edit", "--default-urgency", "urgent").exit_code == 0
    ingest_chunk(hub, pointer_token="default:1")
    ingest_chunk(hub, pointer_token="default:2")
    assert _cli("settings", "edit", "--default-urgency", "standard").exit_code == 0

    refused = _cli("urgency", "retire", "urgent")

    assert refused.exit_code != 0
    assert "urgency urgent carries 2 open chunks" in refused.output
    assert "blizzard hub urgency retire urgent --move-to <SLUG>" in refused.output
    retired = _cli("urgency", "retire", "urgent", "--move-to", "standard")
    assert retired.exit_code == 0, retired.output
    assert _said(retired)[0] == "retired urgency urgent; moved 2 open chunks"
    assert _order() == ["standard"]
    assert _json(_cli("urgency", "get", "standard", "--json"))["open_chunks"] == 2
    urgent_id = _json(_cli("urgency", "get", "urgent", "--json"))["urgency_id"]
    changes = _json(_cli("config", "changes", "--kind", "urgency", "--key", urgent_id, "--json"))["changes"]
    fields = {field["field"]: field["new"] for field in changes[0]["diff"]}
    assert (changes[0]["op"], fields["move_to"], fields["moved_chunks"]) == ("retire", "standard", 2)


def test_retire_refuses_the_default(hub: HubHarness) -> None:
    assert _cli("urgency", "create", "urgent").exit_code == 0

    refused = _cli("urgency", "retire", "standard", "--move-to", "urgent")

    assert refused.exit_code != 0
    assert "standard" in refused.output


def test_enable_order_and_position_reorder_the_set(hub: HubHarness) -> None:
    assert _cli("urgency", "create", "urgent", "--first").exit_code == 0
    assert _cli("urgency", "create", "background").exit_code == 0
    assert _cli("urgency", "retire", "urgent").exit_code == 0
    assert "urgent" not in _order()

    assert _cli("urgency", "enable", "urgent").exit_code == 0
    assert _order() == ["urgent", "standard", "background"]
    assert _cli("urgency", "order", "background", "standard", "urgent").exit_code == 0
    assert _order() == ["background", "standard", "urgent"]
    assert _cli("urgency", "position", "urgent", "--first").exit_code == 0
    assert _order() == ["urgent", "background", "standard"]
    assert _cli("urgency", "order", "standard").exit_code != 0
    assert _cli("urgency", "position", "urgent").exit_code != 0


def test_settings_edit_changes_the_default(hub: HubHarness) -> None:
    assert _cli("urgency", "create", "urgent").exit_code == 0

    edited = _cli("settings", "edit", "--default-urgency", "urgent")

    assert edited.exit_code == 0, edited.output
    assert _said(edited)[0] == "default urgency urgent"
    assert _said(_cli("urgency", "list"))[1].endswith("default")
