"""``blizzard.foundation.operator_sessions.internal.session_file`` — the CLI's local
session-token file (unit tier).

Pins the two acceptance-criteria facts directly: the file (and its parent directory)
are created owner-only, and ``logout`` removes the entry. The real machine's config
dir is never touched — ``conftest``'s suite-wide fixture redirects it already."""

from __future__ import annotations

import json
import stat

import pytest

from blizzard.foundation.operator_sessions.internal.session_file import SessionFile

pytestmark = pytest.mark.unit


def test_load_session_is_none_when_nothing_stored() -> None:
    assert SessionFile.of().load("http://127.0.0.1:8421") is None


def test_save_then_load_round_trips() -> None:
    SessionFile.of().save("http://127.0.0.1:8421", "the-token")
    assert SessionFile.of().load("http://127.0.0.1:8421") == "the-token"


def test_save_session_writes_owner_only_permissions() -> None:
    SessionFile.of().save("http://127.0.0.1:8421", "the-token")
    path = SessionFile.of().path
    file_mode = stat.S_IMODE(path.stat().st_mode)
    dir_mode = stat.S_IMODE(path.parent.stat().st_mode)
    assert file_mode == stat.S_IRUSR | stat.S_IWUSR
    assert dir_mode == stat.S_IRWXU


def test_save_session_keys_by_hub_url_independently() -> None:
    SessionFile.of().save("http://127.0.0.1:8421", "token-a")
    SessionFile.of().save("http://127.0.0.1:9000", "token-b")
    assert SessionFile.of().load("http://127.0.0.1:8421") == "token-a"
    assert SessionFile.of().load("http://127.0.0.1:9000") == "token-b"


def test_delete_session_removes_only_the_named_entry() -> None:
    SessionFile.of().save("http://127.0.0.1:8421", "token-a")
    SessionFile.of().save("http://127.0.0.1:9000", "token-b")

    SessionFile.of().delete("http://127.0.0.1:8421")

    assert SessionFile.of().load("http://127.0.0.1:8421") is None
    assert SessionFile.of().load("http://127.0.0.1:9000") == "token-b"


def test_delete_session_is_a_no_op_when_nothing_is_stored() -> None:
    SessionFile.of().delete("http://127.0.0.1:8421")  # must not raise
    assert SessionFile.of().load("http://127.0.0.1:8421") is None


def test_delete_session_removes_the_file_once_the_last_entry_is_gone() -> None:
    SessionFile.of().save("http://127.0.0.1:8421", "token-a")
    SessionFile.of().delete("http://127.0.0.1:8421")
    assert not SessionFile.of().path.is_file()


@pytest.mark.parametrize(
    ("saved_as", "looked_up_as"),
    [("http://127.0.0.1:8421/", "http://127.0.0.1:8421"), ("http://127.0.0.1:8421", "http://127.0.0.1:8421/")],
)
def test_a_trailing_slash_names_the_same_hub(saved_as: str, looked_up_as: str) -> None:
    SessionFile.of().save(saved_as, "the-token")
    assert SessionFile.of().load(looked_up_as) == "the-token"

    SessionFile.of().delete(looked_up_as)

    assert SessionFile.of().load(saved_as) is None


def test_saving_under_a_trailing_slash_stores_the_stripped_url() -> None:
    SessionFile.of().save("http://127.0.0.1:8421/", "the-token")
    assert json.loads(SessionFile.of().path.read_text()) == {"http://127.0.0.1:8421": "the-token"}


def test_an_entry_written_with_a_trailing_slash_still_resolves() -> None:
    path = SessionFile.of().path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"http://127.0.0.1:8421/": "the-token"}))
    assert SessionFile.of().load("http://127.0.0.1:8421") == "the-token"
