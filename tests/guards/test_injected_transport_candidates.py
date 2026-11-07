from pathlib import Path

from tests.support.injected_auth_inventory import REVIEWED
from tests.support.injected_transport_candidates import Candidate, candidates, inventory_changes


def test_annotated_receivers_and_stored_fields(tmp_path: Path) -> None:
    (tmp_path / "adapter.py").write_text(
        "class Adapter:\n"
        "    def __init__(self, transport: Client):\n"
        "        self.transport = transport\n"
        "    async def run(self, other: Client):\n"
        "        send = other.post\n"
        "        alias = other\n"
        "        alias.get('/x')\n"
        "        getattr(self.transport, 'request')('GET', '/')\n"
    )
    assert candidates(tmp_path) == [
        Candidate("adapter.py", "Adapter.run", "other.post", 5, 15),
        Candidate("adapter.py", "Adapter.run", "alias.get", 7, 8),
        Candidate("adapter.py", "Adapter.run", "getattr(self.transport, 'request')", 8, 8),
    ]


def test_candidates_are_scoped_and_do_not_classify(tmp_path: Path) -> None:
    (tmp_path / "a.py").write_text(
        "class Store:\n"
        "    cache: dict\n"
        "    def read(self, key: str):\n"
        "        return self.cache.get(key)\n"
        "def unrelated(cache):\n"
        "    cache.get('x')\n"
        "def named(cache: dict):\n"
        "    getattr(cache, 'clear')()\n"
        "    getattr(cache, method)()\n"
    )
    assert candidates(tmp_path) == [
        Candidate("a.py", "Store.read", "self.cache.get", 4, 15),
        Candidate("a.py", "named", "getattr(cache, method)", 9, 4),
    ]


def test_local_annotations_and_nested_owners(tmp_path: Path) -> None:
    (tmp_path / "nested.py").write_text(
        "def outer():\n"
        "    def inner():\n"
        "        client: Client = make_client()\n"
        "        callback = getattr(client, 'send')\n"
        "        return callback\n"
    )
    assert candidates(tmp_path) == [Candidate("nested.py", "outer.inner", "getattr(client, 'send')", 4, 19)]


def test_reviewed_auth_internal_candidates() -> None:
    root = Path(__file__).resolve().parents[2] / "src/blizzard/runner/auth/internal"
    assert root.is_dir()
    assert inventory_changes(candidates(root), REVIEWED) == []


def test_inventory_detects_new_duplicate_and_removed_sites(tmp_path: Path) -> None:
    path = tmp_path / "adapter.py"
    path.write_text("def run(client: Client):\n    client.get('/')\n")
    reviewed = {("adapter.py", "run", "client.get"): "reviewed transport"}
    assert inventory_changes(candidates(tmp_path), reviewed) == []
    path.write_text("\n\ndef run(client: Client):\n    client.get('/')\n")
    assert inventory_changes(candidates(tmp_path), reviewed) == []
    path.write_text("def run(client: Client):\n    client.get('/')\n    client.get('/other')\n")
    assert "found 2" in inventory_changes(candidates(tmp_path), reviewed)[0]
    path.write_text("def run(client: Client):\n    getattr(client, method)('/')\n")
    changes = inventory_changes(candidates(tmp_path), reviewed)
    assert any("client.get: found 0" in item for item in changes)
    assert any("getattr(client, method): found 1, reviewed False" in item for item in changes)
