"""The identity check at the head of every tick: the runner drives its leases only while its token
resolves to the runner its store belongs to. A store naming no runner matches any token, and takes its id
from the first registration, never from the check; a token
naming another runner refuses until the right one returns; a hub that does not answer never reads as
a mismatch; a hub with no identity read leaves the registration reply to decide. While refusing, a
token corrected in the runtime dir's ``.env`` is taken up at the next check, unless the process
environment supplied the token. The hub proxy's forward is a first contact too: it runs the same check
before forwarding while nothing in its process has checked, and takes the outage path when the hub does
not answer it."""

from __future__ import annotations

import dataclasses
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pytest import MonkeyPatch
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.runner.app import create_app
from blizzard.runner.composition import held_hub_token
from blizzard.runner.config import DEFAULT_TOKEN_ENV, RunnerConfig
from blizzard.runner.hub.client import IdentityRouteMissing, TokenIdentity, TokenRefusal
from blizzard.runner.hub.held_token import HubTokenHolder
from blizzard.runner.hub.identity import IdentityState, IdentityVerdictHolder
from blizzard.runner.hub.internal.http_hub import HeldTokenAuth, HttpHubClient
from blizzard.runner.hub.internal.token_file import HubTokenFile
from blizzard.runner.lifecycle.identity_check import IdentityCheck, ProxyIdentityCheck
from blizzard.runner.lifecycle.registration import Registration
from blizzard.runner.loop.context import LoopContext
from blizzard.runner.loop.tick import tick
from tests.runner_fakes import (
    FakeHarness,
    FakeHub,
    FakeProbe,
    FakeProvider,
    make_context,
    make_store,
    no_retry_clock,
)
from tests.support import RunnerFleetClient, build_hub

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_RESET = "blizzard runner store reset /runner"


class _HubWithoutIdentityRead(FakeHub):
    """A hub that predates the identity read: it answers that route 404."""

    def identity(self) -> TokenIdentity | TokenRefusal:
        raise IdentityRouteMissing("404 identity")


def _ctx(
    tmp_path: Path, hub: FakeHub, *, registered: bool = True, hub_token: HubTokenHolder | None = None
) -> LoopContext:
    """A context whose hub is gated by an unchecked verdict, as a freshly started runner's is — its store
    bound by the seeded registration when ``registered``, else by the first one."""
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}", runner_id=None)
    return make_context(
        store,
        hub=hub,
        provider=FakeProvider({"e1": "/ws/e1"}),
        harness=FakeHarness(handle=None, verdict=None),  # type: ignore[arg-type]
        probe=FakeProbe(alive=set()),
        clock=FixedClock(_NOW),
        registered=registered,
        identity_verdict=IdentityVerdictHolder(_RESET),
        hub_token=hub_token,
    )


def _buffer_a_fact(ctx: LoopContext) -> None:
    ctx.stores.outbound.enqueue_outbound(
        kind="lease.minted", chunk_id="ch_1", lease_id="lease_1", payload="{}", created_at=_NOW
    )


def _state(ctx: LoopContext) -> IdentityState:
    return ctx.identity_verdict.verdict().state


@pytest.mark.parametrize(
    ("registered", "token_runner", "expected", "store_runner"),
    [
        (False, "r1", IdentityState.MATCHED, None),
        (True, "r1", IdentityState.MATCHED, "r1"),
        (True, "rn_other", IdentityState.MISMATCHED, "r1"),
    ],
    ids=["empty-row-matches", "same-runner", "another-runner"],
)
def test_the_check_compares_the_token_runner_with_the_store_runner(
    tmp_path: Path, registered: bool, token_runner: str, expected: IdentityState, store_runner: str | None
) -> None:
    hub = FakeHub(default_runner_id="r1")
    hub.issued[hub.token] = token_runner
    ctx = _ctx(tmp_path, hub, registered=registered)

    IdentityCheck(ctx).run()

    verdict = ctx.identity_verdict.verdict()
    assert (verdict.state, verdict.store_runner_id, verdict.token_runner_id) == (expected, store_runner, token_runner)
    assert verdict.checked_at == _NOW
    stored = ctx.stores.identity.runner_identity()
    assert (stored.runner_id if stored is not None else None) == store_runner
    assert ctx.identity.current() == stored


def test_an_empty_store_takes_its_id_from_the_first_registration_never_from_the_check(tmp_path: Path) -> None:
    """The process's identity names only a runner that has registered — sign-in and the trace sweep read it as
    such — so the check leaves an empty store empty, and the registration after it adopts the reply's id."""
    hub = FakeHub(default_runner_id="rn_minted")
    ctx = _ctx(tmp_path, hub, registered=False)

    IdentityCheck(ctx).run()

    assert _state(ctx) is IdentityState.MATCHED
    assert (ctx.stores.identity.runner_identity(), ctx.identity.current()) == (None, None)

    Registration(ctx).run()

    adopted = ctx.stores.identity.runner_identity()
    assert adopted is not None and adopted.runner_id == "rn_minted"
    assert ctx.identity.current() == adopted
    assert len(hub.registered) == 1


@pytest.mark.parametrize("start", [IdentityState.UNCHECKED, IdentityState.MATCHED, IdentityState.MISMATCHED])
@pytest.mark.parametrize("answer", ["down", "token-refused"])
def test_a_hub_that_does_not_answer_leaves_the_verdict_as_it_stood(
    tmp_path: Path, start: IdentityState, answer: str
) -> None:
    hub = FakeHub(default_runner_id="r1")
    ctx = _ctx(tmp_path, hub)
    ctx = dataclasses.replace(ctx, identity_verdict=IdentityVerdictHolder(_RESET, state=start))
    if answer == "down":
        hub.down = True
    else:
        hub.issued.clear()

    IdentityCheck(ctx).run()

    assert _state(ctx) is start


def test_a_mismatched_tick_drives_no_lease_and_delivers_nothing_until_the_right_token_returns(tmp_path: Path) -> None:
    hub = FakeHub(default_runner_id="r1")
    hub.issued[hub.token] = "rn_other"
    ctx = _ctx(tmp_path, hub)
    _buffer_a_fact(ctx)

    with capture_logs() as logs:
        tick(ctx)
        tick(ctx)

    assert _state(ctx) is IdentityState.MISMATCHED
    assert hub.registered == []
    assert hub.push_facts_calls == []
    assert hub.peek_queue_calls == 0
    assert hub.claims == []
    assert hub.chunk_statuses_calls == []
    assert ctx.stores.pause.last_daemon_liveness() == _NOW  # liveness keeps running
    warnings = [e for e in logs if e["log_level"] == "warning" and e["event"].startswith("runner refuses")]
    assert len(warnings) == 1  # once per transition, not once per tick
    assert (warnings[0]["store_runner_id"], warnings[0]["token_runner_id"]) == ("r1", "rn_other")
    assert warnings[0]["reset_command"] == _RESET

    hub.issued[hub.token] = "r1"
    tick(ctx)

    assert _state(ctx) is IdentityState.MATCHED
    assert hub.registered != []
    assert [fact.kind for fact in hub.pushed] == ["lease.minted"]


def test_an_outage_from_start_is_never_a_mismatch_and_the_runner_resumes_when_the_hub_returns(tmp_path: Path) -> None:
    hub = FakeHub(default_runner_id="r1")
    hub.down = True
    ctx = _ctx(tmp_path, hub)
    _buffer_a_fact(ctx)

    tick(ctx)

    assert _state(ctx) is IdentityState.UNCHECKED
    assert ctx.stores.identity.runner_identity() is not None  # the row is untouched

    hub.down = False
    tick(ctx)

    assert _state(ctx) is IdentityState.MATCHED
    assert [fact.kind for fact in hub.pushed] == ["lease.minted"]


def test_an_unchecked_runner_calls_the_hub_for_nothing_but_the_identity_read(tmp_path: Path) -> None:
    """A token the hub refuses leaves the verdict unchecked, and the gated hub answers every other
    call as an outage — no registration, no claim, no delivery."""
    hub = FakeHub(default_runner_id="r1")
    hub.issued.clear()
    ctx = _ctx(tmp_path, hub)
    _buffer_a_fact(ctx)

    tick(ctx)

    assert _state(ctx) is IdentityState.UNCHECKED
    assert (hub.registered, hub.push_facts_calls, hub.peek_queue_calls) == ([], [], 0)


def test_a_hub_without_the_identity_read_lets_the_registration_reply_decide(tmp_path: Path) -> None:
    hub = _HubWithoutIdentityRead(default_runner_id="r1")
    hub.issued[hub.token] = "rn_other"
    ctx = _ctx(tmp_path, hub)
    _buffer_a_fact(ctx)

    tick(ctx)
    tick(ctx)

    assert _state(ctx) is IdentityState.MISMATCHED
    assert len(hub.registered) == 2  # the reply is the check, so it is asked every tick
    assert hub.push_facts_calls == []
    stored = ctx.stores.identity.runner_identity()
    assert stored is not None and stored.runner_id == "r1"

    hub.issued[hub.token] = "r1"
    tick(ctx)  # the reply matches mid-tick; the steps after it resume next tick

    assert _state(ctx) is IdentityState.MATCHED
    tick(ctx)
    assert [fact.kind for fact in hub.pushed] == ["lease.minted"]


@pytest.mark.parametrize(
    ("token_runner", "expected"),
    [("rn_store", IdentityState.MATCHED), ("rn_other", IdentityState.MISMATCHED)],
    ids=["own-token", "another-runners-token"],
)
def test_the_check_reads_the_token_runner_from_a_real_hub(
    tmp_path: Path, token_runner: str, expected: IdentityState
) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    ctx = _ctx(tmp_path, FakeHub(default_runner_id="rn_store"))
    real = HttpHubClient(RunnerFleetClient(hub.app, services=hub.services, default_runner_id=token_runner))

    IdentityCheck(dataclasses.replace(ctx, token_identity=real)).run()

    verdict = ctx.identity_verdict.verdict()
    assert (verdict.state, verdict.store_runner_id, verdict.token_runner_id) == (expected, "rn_store", token_runner)


def _identity(runner_id: str) -> httpx.Response:
    return httpx.Response(200, json={"runner_id": runner_id, "runner_name": "runner-local"})


def _proxied(
    tmp_path: Path,
    forwarded: list[str],
    *,
    identity: httpx.Response | None = None,
    verdict: IdentityVerdictHolder | None = None,
) -> tuple[httpx.Response, IdentityVerdictHolder, list[str]]:
    """A fleet-summary read through the hub proxy of a runner whose store names ``r1``; the hub answers the
    identity read with ``identity`` (the token naming ``r1`` unless a test says otherwise), or is
    unreachable when it is a ``503``. Returns the response, the loop's verdict, and the identity reads."""
    asked: list[str] = []
    answer = identity if identity is not None else _identity("r1")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/fleet/identity":
            asked.append(str(request.url))
            return answer
        forwarded.append(str(request.url))
        return httpx.Response(200, json={"ready": 1, "running": 0, "waiting": 0, "needs": 0})

    ctx = _ctx(tmp_path, FakeHub(default_runner_id="r1"))
    held = verdict or ctx.identity_verdict
    config = RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url="http://hub.local:8421")
    app = create_app(
        config,
        hub_proxy_client=httpx.Client(transport=httpx.MockTransport(handler)),
        hub_retry_clock=no_retry_clock(),
        identity_verdict=held,
        identity_check=ProxyIdentityCheck(ctx.stores.identity, held, ctx.clock),
    )
    client = TestClient(app)
    resp = client.get("/api/fleet-summary")
    if resp.status_code == 200:
        assert client.get("/api/fleet-summary").status_code == 200
    return resp, held, asked


def test_an_unchecked_hub_proxy_checks_the_token_first_and_forwards_once_it_names_the_store_runner(
    tmp_path: Path,
) -> None:
    """The API may serve before any tick has checked: the forward is the process's first contact, so it asks
    the hub itself rather than refusing a runner whose token is its own — once, remembering the match —
    and leaves the loop's verdict, which the status reports, untouched."""
    forwarded: list[str] = []
    resp, verdict, asked = _proxied(tmp_path, forwarded)

    assert resp.status_code == 200, resp.text
    assert forwarded == ["http://hub.local:8421/api/fleet/summary"] * 2
    assert asked == ["http://hub.local:8421/api/fleet/identity"]
    assert verdict.verdict().state is IdentityState.UNCHECKED


def test_an_unchecked_hub_proxy_fails_as_an_outage_does_while_the_hub_does_not_answer(tmp_path: Path) -> None:
    """An unchecked runner behaves exactly as during an outage: the forward answers the hub's own failure,
    which a route serving from the runner's pin falls back on — not an identity refusal."""
    forwarded: list[str] = []
    down = httpx.Response(503, json={"detail": "the hub is unreachable"})
    resp, _, _ = _proxied(tmp_path, forwarded, identity=down)

    assert resp.status_code == 503
    assert resp.json()["detail"] == "the hub is unreachable"
    assert forwarded == []


@pytest.mark.parametrize("status_code", [401, 404], ids=["token-refused", "no-identity-read"])
def test_the_hub_proxy_forwards_nothing_while_the_hub_names_no_runner_for_the_token(
    tmp_path: Path, status_code: int
) -> None:
    forwarded: list[str] = []
    refused = httpx.Response(status_code, json={"reason": "unknown", "runner_id": None})
    resp, _, _ = _proxied(tmp_path, forwarded, identity=refused)

    assert resp.status_code == 503
    assert resp.json()["detail"].startswith("runner_identity_unverified")
    assert forwarded == []


def test_an_unchecked_hub_proxy_refuses_a_token_naming_another_runner(tmp_path: Path) -> None:
    forwarded: list[str] = []
    resp, _, _ = _proxied(tmp_path, forwarded, identity=_identity("rn_other"))

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail.startswith("runner_identity_mismatch")
    assert "r1" in detail and "rn_other" in detail
    assert forwarded == []


def test_the_hub_proxy_names_both_runners_and_the_reset_command_while_refusing(tmp_path: Path) -> None:
    verdict = IdentityVerdictHolder(_RESET)
    verdict.record(IdentityState.MISMATCHED, store_runner_id="rn_store", token_runner_id="rn_other", checked_at=_NOW)
    forwarded: list[str] = []
    resp, _, asked = _proxied(tmp_path, forwarded, verdict=verdict)

    assert resp.status_code == 503
    detail = resp.json()["detail"]
    assert detail.startswith("runner_identity_mismatch")
    assert "rn_store" in detail and "rn_other" in detail and _RESET in detail
    assert (forwarded, asked) == ([], [])


def test_the_hub_proxy_forwards_once_the_loop_matched_without_asking(tmp_path: Path) -> None:
    forwarded: list[str] = []
    matched = IdentityVerdictHolder(_RESET, state=IdentityState.MATCHED)
    resp, _, asked = _proxied(tmp_path, forwarded, verdict=matched)

    assert resp.status_code == 200, resp.text
    assert asked == []


_WRONG = "token-of-another-runner"
_RIGHT = "token-of-the-store-runner"


def _runtime_with_env_token(tmp_path: Path, token: str) -> RunnerConfig:
    """A runtime dir whose ``.env`` holds ``token``, as ``runner init`` leaves it, and the config the
    runner started with from it."""
    HubTokenFile.of(tmp_path, DEFAULT_TOKEN_ENV).write(token)
    return RunnerConfig(root=tmp_path, db_url="sqlite://", hub_url="http://hub.local:8421", hub_token=token)


def _refusing(
    tmp_path: Path, monkeypatch: MonkeyPatch, *, from_process_env: bool = False
) -> tuple[LoopContext, FakeHub]:
    """A runner started on another runner's token, refusing after its first tick; its hub sees whichever
    token the runner's held token presents."""
    if from_process_env:
        monkeypatch.setenv(DEFAULT_TOKEN_ENV, _WRONG)
    else:
        monkeypatch.delenv(DEFAULT_TOKEN_ENV, raising=False)
    held = held_hub_token(_runtime_with_env_token(tmp_path, _WRONG))
    hub = FakeHub(default_runner_id="r1")
    hub.issued = {_WRONG: "rn_other", _RIGHT: "r1"}
    hub.presents(held)
    ctx = _ctx(tmp_path, hub, hub_token=held)
    _buffer_a_fact(ctx)
    tick(ctx)
    assert _state(ctx) is IdentityState.MISMATCHED
    return ctx, hub


def test_a_refusing_runner_whose_env_token_is_corrected_resumes_without_a_restart(
    tmp_path: Path, monkeypatch: MonkeyPatch
) -> None:
    ctx, hub = _refusing(tmp_path, monkeypatch)
    assert (hub.registered, hub.push_facts_calls, hub.peek_queue_calls) == ([], [], 0)

    HubTokenFile.of(tmp_path, DEFAULT_TOKEN_ENV).write(_RIGHT)
    with capture_logs() as logs:
        tick(ctx)

    assert hub.token == _RIGHT
    verdict = ctx.identity_verdict.verdict()
    assert (verdict.state, verdict.store_runner_id, verdict.token_runner_id) == (IdentityState.MATCHED, "r1", "r1")
    assert hub.registered != [] and hub.peek_queue_calls > 0
    assert [fact.kind for fact in hub.pushed] == ["lease.minted"]
    assert any(e["event"].startswith("runner token changed") for e in logs)
    assert not [e for e in logs if any(_RIGHT in str(v) or _WRONG in str(v) for v in e.values())]


def test_a_token_from_the_process_environment_is_never_re_read(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    with capture_logs() as logs:
        ctx, hub = _refusing(tmp_path, monkeypatch, from_process_env=True)
        HubTokenFile.of(tmp_path, DEFAULT_TOKEN_ENV).write(_RIGHT)
        tick(ctx)

    assert hub.token == _WRONG
    assert _state(ctx) is IdentityState.MISMATCHED
    assert hub.push_facts_calls == []
    restarts = [e for e in logs if e["event"].startswith("runner token comes from the process environment")]
    assert len(restarts) == 1  # once, with the refusal it explains


@pytest.mark.parametrize("env", [f"{DEFAULT_TOKEN_ENV}={_WRONG}\n", "# no token assigned\n"], ids=["same", "none"])
def test_a_reread_that_names_no_other_token_changes_nothing(tmp_path: Path, monkeypatch: MonkeyPatch, env: str) -> None:
    ctx, hub = _refusing(tmp_path, monkeypatch)
    (tmp_path / ".env").write_text(env)

    with capture_logs() as logs:
        tick(ctx)

    assert hub.token == _WRONG
    assert _state(ctx) is IdentityState.MISMATCHED
    assert hub.push_facts_calls == []
    assert not [e for e in logs if e["event"].startswith("runner token changed")]


@pytest.mark.parametrize("env", ["missing", "unreadable"])
def test_a_missing_or_unreadable_env_leaves_the_verdict_as_it_was(
    tmp_path: Path, monkeypatch: MonkeyPatch, env: str
) -> None:
    ctx, hub = _refusing(tmp_path, monkeypatch)
    (tmp_path / ".env").unlink()
    if env == "unreadable":
        (tmp_path / ".env").mkdir()  # reading it raises an OSError

    tick(ctx)

    assert hub.token == _WRONG
    assert _state(ctx) is IdentityState.MISMATCHED
    assert hub.push_facts_calls == []


def test_the_hub_clients_present_the_token_taken_up_from_env(tmp_path: Path, monkeypatch: MonkeyPatch) -> None:
    """The loop's hub client and the hub proxy both present the held token as it stands at each call."""
    monkeypatch.delenv(DEFAULT_TOKEN_ENV, raising=False)
    config = _runtime_with_env_token(tmp_path, _WRONG)
    held = held_hub_token(config)
    presented: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        presented.append(request.headers.get("Authorization", ""))
        if request.url.path == "/api/fleet/identity":
            return httpx.Response(200, json={"runner_id": "r1", "runner_name": "runner-local"})
        return httpx.Response(200, json={"ready": 1, "running": 0, "waiting": 0, "needs": 0})

    transport = httpx.MockTransport(handler)
    loop_client = HttpHubClient(httpx.Client(base_url=config.hub_url, transport=transport, auth=HeldTokenAuth(held)))
    app = create_app(
        config,
        hub_proxy_client=httpx.Client(transport=transport),
        hub_retry_clock=no_retry_clock(),
        identity_verdict=IdentityVerdictHolder(_RESET, state=IdentityState.MATCHED),
        hub_token=held,
    )
    HubTokenFile.of(tmp_path, DEFAULT_TOKEN_ENV).write(_RIGHT)
    held.reread()

    loop_client.identity()
    assert TestClient(app).get("/api/fleet-summary").status_code == 200
    assert presented == [f"Bearer {_RIGHT}", f"Bearer {_RIGHT}"]
