"""The old installation discovers releases from the checkout's tags."""

import subprocess
from pathlib import Path

import httpx2
import pytest

from tests.harness import old_installation


def test_release_discovery_after_a_shallow_checkout_fetches_full_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    origin = tmp_path / "origin"
    origin.mkdir()

    def git(cwd: Path, *args: str) -> str:
        return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()

    git(origin, "init")
    git(origin, "config", "user.name", "Test")
    git(origin, "config", "user.email", "test@example.com")
    window = origin / "src/blizzard/wire/window.toml"
    window.parent.mkdir(parents=True)
    window.write_text('floor = "0.2"\n')
    git(origin, "add", ".")
    git(origin, "commit", "-m", "release")
    git(origin, "tag", "v0.2.0")
    window.write_text('floor = "0.2"\n# candidate\n')
    git(origin, "commit", "-am", "candidate")
    checkout = tmp_path / "checkout"
    git(tmp_path, "clone", "--depth=1", "--no-tags", origin.as_uri(), str(checkout))
    assert git(checkout, "tag", "--list") == ""
    monkeypatch.chdir(checkout)
    monkeypatch.delenv("BLIZZARD_REHEARSAL_BASELINE", raising=False)

    def respond(request: httpx2.Request) -> httpx2.Response:
        if request.url.path.endswith("/releases/tags/v0.2.0"):
            return httpx2.Response(200, json={"draft": False, "assets": []})
        return httpx2.Response(404)

    client = httpx2.Client

    def mock_client(**kwargs: object) -> httpx2.Client:
        return client(transport=httpx2.MockTransport(respond))

    monkeypatch.setattr(old_installation.httpx2, "Client", mock_client)
    with pytest.raises(RuntimeError, match="no supported published release"):
        old_installation.release(tmp_path / "assets")

    git(checkout, "fetch", "--unshallow", "--tags", "origin")
    assert old_installation.release(tmp_path / "assets") == ("v0.2.0", tmp_path / "assets")
