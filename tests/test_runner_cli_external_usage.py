"""``blizzard runner external-usage probe`` — the diagnostic CLI's SLUG argument.

SLUG is optional, defaulting to the legacy ``anthropic`` declaration every scaffolded runner
still carries, so every pre-existing invocation with no positional argument keeps working."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
from click.testing import CliRunner, Result

from blizzard.cli.main import blizzard
from blizzard.foundation.subscription_miss import SampleMissReason
from blizzard.runner.host_layout import HOST_CONFIG_FILENAME
from tests.runner_init_fakes import init_runner
from tests.worker_http import Bound, bind_transport

pytestmark = pytest.mark.unit


def _runtime(tmp_path: Path) -> Path:
    root = tmp_path / "runner"
    root.mkdir()
    result = init_runner().invoke(blizzard, ["runner", "init", str(root)])
    assert result.exit_code == 0, result.output
    return root


def _probe(root: Path, *args: str, bound: Bound | None = None) -> Result:
    runner = bound.runner if bound is not None else CliRunner()
    return runner.invoke(blizzard, ["runner", "external-usage", "probe", *args, "--dir", str(root)])


def test_no_slug_argument_resolves_the_legacy_anthropic_declaration(tmp_path: Path) -> None:
    root = _runtime(tmp_path)

    result = _probe(root)

    assert result.exit_code == 0, result.output
    assert "no known sampler binding" not in result.output


def test_an_unknown_slug_still_errors_by_name(tmp_path: Path) -> None:
    root = _runtime(tmp_path)

    result = _probe(root, "mystery")

    assert result.exit_code != 0
    assert "mystery" in result.output


_FAR_FUTURE_MILLIS = 4_102_444_800_000


def _declare_credentials(root: Path, credentials: Path) -> None:
    """Point the scaffolded ``anthropic`` subscription at ``credentials``."""
    config = root / HOST_CONFIG_FILENAME
    config.write_text(
        config.read_text().replace(
            'provider = "anthropic"\n', f'provider = "anthropic"\ncredentials_path = "{credentials}"\n'
        )
    )


def _credentials(tmp_path: Path, name: str, expires_at_millis: int) -> Path:
    path = tmp_path / name
    path.write_text(json.dumps({"claudeAiOauth": {"accessToken": "tok", "expiresAt": expires_at_millis}}))
    return path


def _unreachable(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


@pytest.mark.parametrize(
    ("reason", "expected"),
    [
        (SampleMissReason.CREDENTIAL_LAPSED, "no sample: credential lapsed: log in again"),
        (SampleMissReason.CREDENTIAL_UNREADABLE, "no sample: credential unreadable"),
        (SampleMissReason.ENDPOINT_UNREACHABLE, "no sample: endpoint unreachable"),
        (SampleMissReason.RESPONSE_UNPARSEABLE, "no sample: response unparseable"),
    ],
)
def test_a_miss_prints_its_own_distinguishable_reason(tmp_path: Path, reason: SampleMissReason, expected: str) -> None:
    root = _runtime(tmp_path)
    live = _credentials(tmp_path, "live.json", _FAR_FUTURE_MILLIS)
    credentials, handler = {
        SampleMissReason.CREDENTIAL_LAPSED: (
            _credentials(tmp_path, "lapsed.json", 1_000),
            lambda request: httpx.Response(200),
        ),
        SampleMissReason.CREDENTIAL_UNREADABLE: (tmp_path / "absent.json", lambda request: httpx.Response(200)),
        SampleMissReason.ENDPOINT_UNREACHABLE: (live, _unreachable),
        SampleMissReason.RESPONSE_UNPARSEABLE: (live, lambda request: httpx.Response(200, content=b"not json")),
    }[reason]
    _declare_credentials(root, credentials)

    result = _probe(root, bound=bind_transport(handler))

    assert result.exit_code == 0, result.output
    assert expected in result.output
