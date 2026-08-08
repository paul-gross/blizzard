"""``blizzard.hub.session_store`` — the CLI's local session-token file (unit tier,
issue #96).

Pins the two acceptance-criteria facts directly: the file (and its parent directory)
are created owner-only, and ``logout`` removes the entry. The real machine's config
dir is never touched — ``conftest``'s suite-wide fixture redirects it already."""

from __future__ import annotations

import stat

import pytest

from blizzard.hub.session_store import SessionFile

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
