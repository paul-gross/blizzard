"""The ``github.app-installation`` binding's authorization (unit tier), against a GitHub App's side of GitHub on a
mock transport and a controlled clock: a token is minted once, held, renewed inside five minutes of expiring, minted
afresh after a restart and under a replaced key, dropped and retried once on a 401, never shared across tenants,
forges, installations or scopes; the installation's grant narrows what it offers; every mint and installation-read
refusal classifies as its own outcome on the landing path, the work-source path, and at build; and no refusal, log or
representation carries the JWT, the key, or a token."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta

import httpx2
import pytest

from blizzard.foundation.clock import ControlledClock
from blizzard.hub.domain.chunk.model import WorkRef
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.credentials import ResolvedCredential
from blizzard.hub.domain.config.forges import FORGE_KIND_CAPABILITIES
from blizzard.hub.domain.kernel.forge_refusals import AuthRefusal, ForgeMintRefused, refusal_remedy
from blizzard.hub.forge.auth import ForgeAuthorization, ForgeScope
from blizzard.hub.forge.internal.authorizations import ForgeAuthorizations
from blizzard.hub.forge.internal.github_auth import (
    APP_OPTIONAL_PERMISSIONS,
    APP_REQUIRED_PERMISSIONS,
    RENEW_BEFORE,
    InstallationTokens,
)
from blizzard.hub.forge.internal.github_landing import GitHubLanding
from blizzard.hub.forge.landing import ForgeAuthRefused, ForgeNotFound, ForgeObject
from blizzard.hub.forge.rates import rate_dialect
from blizzard.hub.forge.registry import ForgeBinding
from blizzard.hub.work_sources.internal.github_work_source import GitHubWorkSource
from blizzard.hub.work_sources.source import WorkSourceAuthRefused
from tests.support.github_app import (
    API_BASE,
    FULL_GRANT,
    INSTALLATION_ID,
    WITH_APP,
    AppGitHub,
    AppKeyReader,
    Forges,
    app_credential,
    app_key_pem,
)

_NOW = datetime(2026, 11, 14, 12, 0, 0, tzinfo=UTC)
_KIND = FORGE_KIND_CAPABILITIES["github"]


class _World:
    """One tenant's boundary over the fake GitHub, its key reader, and its clock."""

    def __init__(self, github: AppGitHub | None = None, clock: ControlledClock | None = None) -> None:
        self.clock = clock or ControlledClock(_NOW)
        self.github = github or AppGitHub(clock=self.clock)
        self.reader = AppKeyReader(self.github.private_pem)
        self.tokens = InstallationTokens(clock=self.clock, transport=self.github.transport)
        self.authorizations = ForgeAuthorizations(
            secrets=self.reader, forges=Forges(), tokens=self.tokens, bindings=WITH_APP
        )

    def restarted(self) -> _World:
        """The same GitHub and clock behind a fresh tenant — a hub restart."""
        return _World(self.github, self.clock)

    def authorization(
        self, credential: ResolvedCredential | None = None, scope: ForgeScope = ForgeScope.LANDING
    ) -> ForgeAuthorization:
        return self.authorizations.authorization(credential or app_credential(), scope)

    def get(self, authorization: ForgeAuthorization) -> httpx2.Response:
        """One request through a sync client authenticated by ``authorization``."""
        with httpx2.Client(base_url=API_BASE, auth=authorization.auth, transport=self.github.transport) as client:
            return client.get("/repos/acme/widget")

    def sent_token(self) -> str:
        return self.github.sent[-1].removeprefix("token ")


@pytest.fixture
def world() -> _World:
    return _World()


# --- minting, holding, renewing ------------------------------------------------------------------------------------


def test_a_token_is_minted_once_and_sent_until_it_nears_expiry(world: _World) -> None:
    authorization = world.authorization()

    assert world.get(authorization).status_code == 200
    first = world.sent_token()
    world.clock.advance(timedelta(hours=1) - RENEW_BEFORE - timedelta(seconds=1))
    assert world.get(authorization).status_code == 200

    assert (len(world.github.mints), world.sent_token()) == (1, first)


def test_a_token_within_five_minutes_of_expiring_is_renewed_on_the_next_request(world: _World) -> None:
    authorization = world.authorization()
    world.get(authorization)
    first = world.sent_token()

    world.clock.advance(timedelta(hours=1) - RENEW_BEFORE)
    assert world.get(authorization).status_code == 200

    assert len(world.github.mints) == 2 and world.sent_token() != first


def test_a_token_minted_already_inside_the_renewal_window_is_sent_rather_than_minted_again() -> None:
    clock = ControlledClock(_NOW)
    world = _World(AppGitHub(clock=clock, lifetime=timedelta(minutes=2)), clock)
    authorization = world.authorization()

    assert world.get(authorization).status_code == 200

    assert len(world.github.mints) == 1


def test_concurrent_requests_from_threads_and_the_event_loop_cause_one_mint() -> None:
    clock = ControlledClock(_NOW)
    world = _World(AppGitHub(clock=clock, mint_delay=0.2), clock)
    authorization = world.authorization()
    statuses: list[int] = []

    def from_a_thread() -> None:
        statuses.append(world.get(authorization).status_code)

    async def from_the_loop() -> list[int]:
        async with httpx2.AsyncClient(
            base_url=API_BASE, auth=authorization.auth, transport=world.github.transport
        ) as client:
            answers = await asyncio.gather(*(client.get("/repos/acme/widget") for _ in range(5)))
        return [answer.status_code for answer in answers]

    threads = [threading.Thread(target=from_a_thread) for _ in range(5)]
    for thread in threads:
        thread.start()
    statuses.extend(asyncio.run(from_the_loop()))
    for thread in threads:
        thread.join()

    assert statuses == [200] * 10
    assert len(world.github.mints) == 1


def test_the_event_loop_is_not_held_while_a_token_is_minted() -> None:
    clock = ControlledClock(_NOW)
    world = _World(AppGitHub(clock=clock, mint_delay=0.3), clock)
    authorization = world.authorization()
    ticks: list[int] = []

    async def go() -> None:
        async def tick() -> None:
            for count in range(5):
                ticks.append(count)
                await asyncio.sleep(0.02)

        async with httpx2.AsyncClient(
            base_url=API_BASE, auth=authorization.auth, transport=world.github.transport
        ) as client:
            await asyncio.gather(client.get("/repos/acme/widget"), tick())

    asyncio.run(go())

    assert ticks == [0, 1, 2, 3, 4]


def test_a_restarted_hub_mints_on_first_use(world: _World) -> None:
    world.get(world.authorization())

    again = world.restarted()
    assert again.get(again.authorization()).status_code == 200

    assert len(world.github.mints) == 2


def test_a_replaced_key_mints_under_a_new_key_and_reads_the_installation_again(world: _World) -> None:
    world.get(world.authorization())
    first = world.sent_token()

    world.reader.revision = 2
    assert world.get(world.authorization()).status_code == 200

    assert (len(world.github.mints), world.github.reads) == (2, 2)
    assert world.sent_token() != first


def test_the_installation_is_read_once_per_key_revision(world: _World) -> None:
    world.authorization()
    world.authorization(scope=ForgeScope.WORK_SOURCE)
    world.authorization()

    assert world.github.reads == 1


# --- a refused token ------------------------------------------------------------------------------------------------


def test_a_401_drops_the_token_and_the_request_goes_once_more_with_a_fresh_one(
    world: _World, caplog: pytest.LogCaptureFixture
) -> None:
    authorization = world.authorization()
    world.get(authorization)
    revoked = world.sent_token()
    world.github.revoke(revoked)

    with caplog.at_level(logging.WARNING):
        answer = world.get(authorization)

    assert answer.status_code == 200
    assert len(world.github.mints) == 2 and world.sent_token() != revoked
    assert revoked not in caplog.text


def test_a_second_401_is_the_answer() -> None:
    clock = ControlledClock(_NOW)
    world = _World(AppGitHub(clock=clock, always_401=True), clock)

    answer = world.get(world.authorization())

    assert answer.status_code == 401
    assert (len(world.github.mints), len(world.github.sent)) == (2, 2)


def test_a_second_401_on_the_landing_path_is_credential_invalid() -> None:
    clock = ControlledClock(_NOW)
    world = _World(AppGitHub(clock=clock, always_401=True), clock)

    with pytest.raises(ForgeAuthRefused) as refused:
        _land(world, world.authorization(), lambda forge: forge.branch_tip("main"))

    assert refused.value.refusal is AuthRefusal.CREDENTIAL_INVALID
    assert len(world.github.mints) == 2


# --- what never shares a token --------------------------------------------------------------------------------------


def test_two_tenants_never_share_a_token(world: _World) -> None:
    other = world.restarted()

    world.get(world.authorization())
    mine = world.sent_token()
    other.get(other.authorization())

    assert other.sent_token() != mine and len(world.github.mints) == 2


@pytest.mark.parametrize(
    ("credential", "scope"),
    [
        (app_credential(forge_id="frg_other"), ForgeScope.LANDING),
        (app_credential(installation_id=INSTALLATION_ID + 1), ForgeScope.LANDING),
        (app_credential(), ForgeScope.WORK_SOURCE),
    ],
    ids=["another-forge", "another-installation", "another-scope"],
)
def test_another_forge_installation_or_scope_never_shares_a_token(
    world: _World, credential: ResolvedCredential, scope: ForgeScope
) -> None:
    world.github.grant(FULL_GRANT, installation_id=INSTALLATION_ID + 1)
    world.get(world.authorization())
    mine = world.sent_token()

    world.get(world.authorization(credential, scope))

    assert world.sent_token() != mine and len(world.github.mints) == 2


def test_each_scope_is_minted_with_its_permissions(world: _World) -> None:
    world.get(world.authorization())
    world.get(world.authorization(scope=ForgeScope.WORK_SOURCE))

    landing, work_source = (mint["permissions"] for mint in world.github.mints)
    assert landing == {
        **APP_REQUIRED_PERMISSIONS[ForgeScope.LANDING],
        "checks": "read",
        "statuses": "read",
        "actions": "write",
    }
    assert work_source == dict(APP_REQUIRED_PERMISSIONS[ForgeScope.WORK_SOURCE])


# --- what an installation offers ------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("withdrawn", "withheld"),
    [
        ((), frozenset[Capability]()),
        (("checks",), frozenset({Capability.CHECKS_READ})),
        (("statuses",), frozenset({Capability.CHECKS_READ})),
        (("actions",), frozenset({Capability.CHECKS_RERUN})),
        (("checks", "actions"), frozenset({Capability.CHECKS_READ, Capability.CHECKS_RERUN})),
    ],
)
def test_the_installation_grant_narrows_what_a_landing_authorization_offers(
    world: _World, withdrawn: tuple[str, ...], withheld: frozenset[Capability]
) -> None:
    world.github.grant({k: v for k, v in FULL_GRANT.items() if k not in withdrawn})

    authorization = world.authorization()

    assert authorization.offered == _KIND - withheld
    assert (authorization.binding, authorization.variant) == ("github.app-installation", None)
    assert authorization.authority == f"app/12/installation/{INSTALLATION_ID}"


def test_a_grant_below_the_level_an_optional_permission_needs_withholds_its_capability(world: _World) -> None:
    world.github.grant({**FULL_GRANT, "actions": "read"})

    assert Capability.CHECKS_RERUN not in world.authorization().offered


def test_a_work_source_authorization_offers_the_whole_kind(world: _World) -> None:
    world.github.grant({k: v for k, v in FULL_GRANT.items() if k not in ("checks", "statuses", "actions")})

    assert world.authorization(scope=ForgeScope.WORK_SOURCE).offered == _KIND
    assert APP_OPTIONAL_PERMISSIONS[ForgeScope.WORK_SOURCE] == {}


def test_a_withheld_capability_refused_by_the_forge_is_a_missing_permission_not_an_unsupported_token() -> None:
    clock = ControlledClock(_NOW)
    github = AppGitHub(clock=clock)
    github.grant({k: v for k, v in FULL_GRANT.items() if k != "checks"})
    world = _World(github, clock)
    authorization = world.authorization()
    world.get(authorization)
    routes = {"/repos/acme/widget/commits/abc/check-runs": httpx2.Response(403, json={"message": "no"})}

    with pytest.raises(ForgeAuthRefused) as refused:
        _land(world, authorization, lambda forge: forge.checks("abc"), routes)

    assert refused.value.refusal is AuthRefusal.PERMISSION_MISSING


def test_a_repository_the_installation_cannot_see_says_so() -> None:
    world = _World()
    authorization = world.authorization()
    routes = {
        "/repos/acme/widget/git/ref/heads/main": httpx2.Response(404, json={"message": "Not Found"}),
        "/repos/acme/widget": httpx2.Response(404, json={"message": "Not Found"}),
    }

    with pytest.raises(ForgeNotFound) as missing:
        _land(world, authorization, lambda forge: forge.branch_tip("main"), routes)

    assert missing.value.what is ForgeObject.REPOSITORY
    assert "or the installation does not include it" in str(missing.value)


# --- refusals: mint, installation read, and build ------------------------------------------------------------------

_MINT_REFUSALS = [
    (401, AuthRefusal.KEY_REJECTED),
    (403, AuthRefusal.INSTALLATION_SUSPENDED),
    (404, AuthRefusal.INSTALLATION_MISSING),
    (422, AuthRefusal.PERMISSION_MISSING),
]


@pytest.mark.parametrize(("status", "refusal"), _MINT_REFUSALS)
def test_a_refused_mint_on_the_landing_path_is_its_refusal(
    world: _World, status: int, refusal: AuthRefusal, caplog: pytest.LogCaptureFixture
) -> None:
    authorization = world.authorization()
    world.github.mint_status = status

    with caplog.at_level(logging.DEBUG), pytest.raises(ForgeAuthRefused) as refused:
        _land(world, authorization, lambda forge: forge.branch_tip("main"))

    assert refused.value.refusal is refusal
    diagnostic = refused.value.diagnostic
    assert (diagnostic.operation, diagnostic.repository, diagnostic.credential) == ("mint_token", "acme/widget", "app")
    _assert_no_secret(world, f"{refused.value!r} {refused.value} {diagnostic!r} {caplog.text}")


@pytest.mark.parametrize(("status", "refusal"), _MINT_REFUSALS)
def test_a_refused_mint_on_the_work_source_path_is_its_refusal(
    world: _World, status: int, refusal: AuthRefusal, caplog: pytest.LogCaptureFixture
) -> None:
    authorization = world.authorization(scope=ForgeScope.WORK_SOURCE)
    world.github.mint_status = status

    with caplog.at_level(logging.DEBUG), pytest.raises(WorkSourceAuthRefused) as refused:
        _work_source(world, authorization).fetch(WorkRef(source="wsrc_1", ref="7"))

    assert refused.value.refusal is refusal
    assert refused.value.diagnostic.repository == "acme/widget"
    _assert_no_secret(world, f"{refused.value!r} {refused.value} {refused.value.diagnostic!r} {caplog.text}")


def test_a_mint_refused_for_a_missing_permission_names_the_permissions_the_installation_lacks(world: _World) -> None:
    world.github.grant({**FULL_GRANT, "contents": "read"})
    authorization = world.authorization()
    world.github.mint_status = 422

    with pytest.raises(ForgeAuthRefused) as refused:
        _land(world, authorization, lambda forge: forge.branch_tip("main"))

    diagnostic = refused.value.diagnostic
    assert diagnostic.accepted_permissions == "contents=write"
    assert (
        refusal_remedy(refused.value.refusal, diagnostic)
        == "Grant contents=write to the token or the App installation."
    )


def _read_answers_401(github: AppGitHub) -> None:
    github.read_status = 401


def _read_answers_404(github: AppGitHub) -> None:
    github.read_status = 404


@pytest.mark.parametrize(
    ("arrange", "refusal"),
    [
        (_read_answers_401, AuthRefusal.KEY_REJECTED),
        (_read_answers_404, AuthRefusal.INSTALLATION_MISSING),
        (AppGitHub.suspend, AuthRefusal.INSTALLATION_SUSPENDED),
    ],
    ids=["key-rejected", "installation-missing", "installation-suspended"],
)
def test_a_refused_installation_read_refuses_the_build(
    world: _World, arrange: Callable[[AppGitHub], None], refusal: AuthRefusal
) -> None:
    arrange(world.github)

    with pytest.raises(ForgeMintRefused) as refused:
        world.authorization()

    assert refused.value.classified.refusal is refusal
    assert refused.value.classified.diagnostic.operation == "read_installation"
    _assert_no_secret(world, f"{refused.value!r} {refused.value} {refused.value.classified!r}")


def test_a_key_github_does_not_hold_is_key_rejected(world: _World) -> None:
    world.reader.pem = app_key_pem(1)

    with pytest.raises(ForgeMintRefused) as refused:
        world.authorization()

    assert refused.value.classified.refusal is AuthRefusal.KEY_REJECTED


def test_a_value_that_is_no_private_key_is_key_rejected_without_quoting_it(world: _World) -> None:
    world.reader.pem = "-----BEGIN PRIVATE KEY-----\nnot-a-key\n-----END PRIVATE KEY-----\n"

    with pytest.raises(ForgeMintRefused) as refused:
        world.authorization()

    assert refused.value.classified.refusal is AuthRefusal.KEY_REJECTED
    assert "not-a-key" not in f"{refused.value!r} {refused.value} {refused.value.classified!r}"
    assert refused.value.__cause__ is None and refused.value.__suppress_context__


def test_an_authorization_holds_no_key_or_token_in_its_representation(world: _World) -> None:
    authorization = world.authorization()
    world.get(authorization)

    _assert_no_secret(world, f"{authorization!r} {authorization.auth!r} {world.tokens!r}")


# --- helpers --------------------------------------------------------------------------------------------------------


def _assert_no_secret(world: _World, text: str) -> None:
    secrets = [*world.github.jwts, *world.github.tokens, world.github.private_pem]
    assert not [secret for secret in secrets if secret and secret in text]
    assert "BEGIN" not in text and "ghs_" not in text and "eyJ" not in text


def _land(
    world: _World,
    authorization: ForgeAuthorization,
    call: Callable[[GitHubLanding], Awaitable[object]],
    routes: dict[str, httpx2.Response] | None = None,
) -> object:
    """``call`` on a ``GitHubLanding`` for ``acme/widget`` acting with ``authorization``, its non-App routes answered
    from ``routes`` where named, else by the fake."""
    fake = world.github.transport

    def handle(request: httpx2.Request) -> httpx2.Response:
        if (
            routes is not None
            and request.url.path in routes
            and request.headers.get("Authorization", "").startswith("token ")
        ):
            return routes[request.url.path]
        return fake.handle_request(request)

    lock = asyncio.Lock()
    binding = ForgeBinding(
        kind="github",
        api_url=API_BASE,
        owner="acme",
        repo="widget",
        authorization=authorization,
        write_lock=lambda: lock,
        dialect=rate_dialect("github"),
        learn=lambda _reading: None,
    )

    async def nap(_seconds: float) -> None:
        return None

    forge = GitHubLanding(binding, clock=world.clock, sleep=nap, transport=httpx2.MockTransport(handle))

    async def go() -> object:
        try:
            return await call(forge)
        finally:
            forge.close()
            await asyncio.sleep(0)

    return asyncio.run(go())


def _work_source(world: _World, authorization: ForgeAuthorization) -> GitHubWorkSource:
    client = httpx2.Client(base_url=API_BASE, auth=authorization.auth, transport=world.github.transport)
    return GitHubWorkSource(
        client, source_id="wsrc_1", slug="issues", repo="acme/widget", web_base="https://github.test"
    )
