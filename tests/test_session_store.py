"""``blizzard.hub.session_store`` — the CLI's local session-token file (unit tier,
issue #96).

Pins the two acceptance-criteria-load-bearing facts directly: the file (and its
parent directory) are created owner-only, and ``logout`` (``delete_session``) removes
the entry so it stops being sent. ``platformdirs.user_config_dir`` is monkeypatched to
a ``tmp_path`` so this never touches the real machine's config dir.
"""

from __future__ import annotations

import stat
from pathlib import Path

import pytest

from blizzard.hub import session_store

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolated_config_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    config_dir = tmp_path / "config" / "blizzard"
    monkeypatch.setattr(session_store.platformdirs, "user_config_dir", lambda _app: str(config_dir))
    return config_dir


def test_load_session_is_none_when_nothing_stored() -> None:
    assert session_store.load_session("http://127.0.0.1:8421") is None


def test_save_then_load_round_trips() -> None:
    session_store.save_session("http://127.0.0.1:8421", "the-token")
    assert session_store.load_session("http://127.0.0.1:8421") == "the-token"


def test_save_session_writes_owner_only_permissions() -> None:
    session_store.save_session("http://127.0.0.1:8421", "the-token")
    path = session_store._sessions_path()
    file_mode = stat.S_IMODE(path.stat().st_mode)
    dir_mode = stat.S_IMODE(path.parent.stat().st_mode)
    assert file_mode == stat.S_IRUSR | stat.S_IWUSR
    assert dir_mode == stat.S_IRWXU


def test_save_session_keys_by_hub_url_independently() -> None:
    session_store.save_session("http://127.0.0.1:8421", "token-a")
    session_store.save_session("http://127.0.0.1:9000", "token-b")
    assert session_store.load_session("http://127.0.0.1:8421") == "token-a"
    assert session_store.load_session("http://127.0.0.1:9000") == "token-b"


def test_delete_session_removes_only_the_named_entry() -> None:
    session_store.save_session("http://127.0.0.1:8421", "token-a")
    session_store.save_session("http://127.0.0.1:9000", "token-b")

    session_store.delete_session("http://127.0.0.1:8421")

    assert session_store.load_session("http://127.0.0.1:8421") is None
    assert session_store.load_session("http://127.0.0.1:9000") == "token-b"


def test_delete_session_is_a_no_op_when_nothing_is_stored() -> None:
    session_store.delete_session("http://127.0.0.1:8421")  # must not raise
    assert session_store.load_session("http://127.0.0.1:8421") is None


def test_delete_session_removes_the_file_once_the_last_entry_is_gone() -> None:
    session_store.save_session("http://127.0.0.1:8421", "token-a")
    session_store.delete_session("http://127.0.0.1:8421")
    assert not session_store._sessions_path().is_file()
