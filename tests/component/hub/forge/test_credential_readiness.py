"""A tenant's credential check (component tier), over a migrated store, its live authorization boundary, and the
GitHub probe on a recording transport: which uses a check takes by default and by name, the refusal of a use acting
with another credential, a mint refused before any use, an installation the forge does not answer for, the deadline
bounding the authorization's build, the one ``credential-check`` event, and nothing else written."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import httpx2
import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock, ManualMonotonicClock
from blizzard.foundation.credential_check import CheckOutcome
from blizzard.foundation.secrets.factories import secret_cipher
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.composition import build_hub_core, build_live_config, connections_for_engine
from blizzard.hub.domain.chunk.event_log import EventLogService
from blizzard.hub.domain.config.changes import RecordKind, RecordRef
from blizzard.hub.domain.config.credential_resolution import CredentialChoice
from blizzard.hub.domain.config.credentials import (
    ConfiguredCredential,
    CredentialDraft,
    CredentialNotUsedBy,
    ResolvedCredential,
)
from blizzard.hub.domain.config.forge_resolution import ForgeChoice
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryDraft
from blizzard.hub.domain.config.secrets import SecretName
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, WorkSourceDraft
from blizzard.hub.domain.kernel.forge_refusals import (
    AuthRefusal,
    ClassifiedRefusal,
    ForgeAccessUnavailable,
    ForgeDiagnostic,
    ForgeMintRefused,
)
from blizzard.hub.events.broker import EventBroker
from blizzard.hub.forge.auth import ForgeAuthorization, ForgeScope, IForgeAuthorizations
from blizzard.hub.forge.budget import ForgeBudgets
from blizzard.hub.forge.internal.github_readiness import GitHubReadinessProbe
from blizzard.hub.forge.rates import rate_dialect
from blizzard.hub.forge.readiness import CredentialReadiness
from blizzard.hub.secrets import hub_key_provider
from blizzard.hub.store import schema
from blizzard.hub.tenancy import StoreScope
from tests.support.scaffolding import OP, config_authoring, default_tenant, migrated_hub_at, seed_forge

_NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
_TIP = "c0ffee0" + "0" * 33


def _answer(request: httpx2.Request) -> httpx2.Response:
    path = request.url.path
    body: dict[str, object] = {}
    if "/git/ref/heads/" in path:
        body = {"object": {"sha": _TIP}}
    elif path.endswith("/check-runs"):
        body = {"total_count": 1}
    return httpx2.Response(200, content=json.dumps(body).encode(), headers={"X-OAuth-Scopes": "repo"})


class _RefusingMint:
    """An authorization boundary whose every build is refused at the installation's mint."""

    def authorization(
        self, credential: ResolvedCredential, scope: ForgeScope, *, call_timeout: Callable[[], float] | None = None
    ) -> ForgeAuthorization:
        del credential, scope, call_timeout
        diagnostic = ForgeDiagnostic(
            status=404,
            operation="read_installation",
            endpoint="GET /app/installations/{installation_id}",
            repository=None,
            message="Not Found",
            documentation_url=None,
            request_id=None,
            accepted_permissions=None,
        )
        raise ForgeMintRefused(ClassifiedRefusal(AuthRefusal.INSTALLATION_MISSING, diagnostic))


class _Unanswered:
    """An authorization boundary whose every build finds the forge not answering for the installation."""

    def authorization(
        self, credential: ResolvedCredential, scope: ForgeScope, *, call_timeout: Callable[[], float] | None = None
    ) -> ForgeAuthorization:
        del credential, scope, call_timeout
        raise ForgeAccessUnavailable("the installation could not be read: ReadTimeout")


class _SlowBuild:
    """The live boundary behind a build taking ``seconds`` of the check's clock, recording the bound each build was
    handed, read when the build ends."""

    def __init__(self, inner: IForgeAuthorizations, clock: ManualMonotonicClock, seconds: float) -> None:
        self.inner = inner
        self.clock = clock
        self.seconds = seconds
        self.bounds: list[float] = []

    def authorization(
        self, credential: ResolvedCredential, scope: ForgeScope, *, call_timeout: Callable[[], float] | None = None
    ) -> ForgeAuthorization:
        assert call_timeout is not None
        self.bounds.append(call_timeout())
        self.clock.advance(self.seconds)
        self.bounds.append(call_timeout())
        return self.inner.authorization(credential, scope, call_timeout=call_timeout)


class _World:
    def __init__(self, tmp_path: Path) -> None:
        db_url = f"sqlite:///{tmp_path / 'hub.db'}"
        migrated_hub_at(db_url)
        self.engine = create_engine_from_url(db_url)
        keys = hub_key_provider({}, data_dir=tmp_path / "data")
        clock = FixedClock(_NOW)
        self.stores = build_hub_core(connections_for_engine(self.engine), clock=clock).open(
            StoreScope(default_tenant(self.engine))
        )
        self.authoring = config_authoring(self.engine, keys=keys, clock=clock)
        forge_id = seed_forge(self.engine, "http://forge.fixture")
        forge = self.stores.forge_records.get(forge_id)
        assert forge is not None
        self.forge_id = forge_id
        self.forge = ForgeChoice(forge=forge_id, named=forge)
        self.deploy = self._credential("deploy")
        self.other = self._credential("other")
        self.widget = self._repository("widget", self.deploy)
        self.gadget = self._repository("gadget", self.deploy)
        self.elsewhere = self._repository("elsewhere", self.other)
        self.issues = self._work_source("issues", self.deploy)
        self.requests: list[str] = []
        self.event_log = EventLogService(events=self.stores.chunk_stores.events, publisher=EventBroker())
        self.live = build_live_config(self.stores, cipher=secret_cipher(keys))

    def _credential(self, slug: str) -> ConfiguredCredential:
        self.authoring.create_secret(SecretName.parse(slug), f"ghp_{slug}{'0' * 30}", OP)
        draft = CredentialDraft(
            slug=slug,
            name=None,
            forge_id=self.forge_id,
            binding="github.token",
            secret_name=slug,
            settings={"variant": "classic"},
        )
        return self.authoring.create_credential(draft, OP)

    def _repository(self, repo: str, credential: ConfiguredCredential) -> ConfiguredRepository:
        draft = RepositoryDraft(owner="acme", repo=repo, base_branch="main", secret_name=None)
        choice = CredentialChoice(credential=credential.credential_id, named=credential)
        return self.authoring.create_repository_naming(repo, None, draft, self.forge, OP, credential=choice).record

    def _work_source(self, slug: str, credential: ConfiguredCredential) -> ConfiguredWorkSource:
        draft = WorkSourceDraft(provider="github", locator="acme/widget", annotate=False, secret=None)
        choice = CredentialChoice(credential=credential.credential_id, named=credential)
        return self.authoring.create_work_source_naming(slug, None, draft, self.forge, OP, credential=choice).record

    def _forge(self, request: httpx2.Request) -> httpx2.Response:
        self.requests.append(f"{request.method} {request.url.path}")
        return _answer(request)

    def readiness(
        self, *, authorizations: IForgeAuthorizations | None = None, monotonic: ManualMonotonicClock | None = None
    ) -> CredentialReadiness:
        return CredentialReadiness(
            repositories=self.stores.repository_records,
            work_sources=self.stores.work_source_records,
            forges=self.stores.forge_records,
            authorizations=authorizations or self.live.authorizations,
            budgets=ForgeBudgets(clock=FixedClock(_NOW), dialects=rate_dialect),
            probes={"github": GitHubReadinessProbe(transport=httpx2.MockTransport(self._forge))},
            event_log=self.event_log,
            clock=FixedClock(_NOW),
            monotonic=monotonic or ManualMonotonicClock(),
        )

    def events(self) -> list[sa.Row[tuple[str, str, str, object]]]:
        with self.engine.connect() as conn:
            query = sa.select(
                schema.event_log.c.kind,
                schema.event_log.c.severity,
                schema.event_log.c.message,
                schema.event_log.c.detail,
            ).where(schema.event_log.c.tenant_id == default_tenant(self.engine))
            return list(conn.execute(query).all())

    def config_rows(self) -> int:
        with self.engine.connect() as conn:
            return conn.execute(
                sa.select(sa.func.count())
                .select_from(schema.config_changes)
                .where(schema.config_changes.c.tenant_id == default_tenant(self.engine))
            ).scalar_one()


@pytest.fixture
def world(tmp_path: Path) -> _World:
    return _World(tmp_path)


def test_a_check_with_no_scope_takes_every_enabled_record_acting_with_the_credential(world: _World) -> None:
    world.authoring.retire_repository(world.gadget, OP)

    check = world.readiness().check(world.deploy)

    assert [(use.kind, use.slug) for use in check.uses] == [("repository", "widget"), ("work_source", "issues")]
    assert all(use.reached for use in check.uses)
    assert (check.credential, check.binding, check.variant) == ("deploy", "github.token", "classic")
    assert check.mint is None


def test_named_uses_are_taken_by_id_or_slug(world: _World) -> None:
    check = world.readiness().check(world.deploy, repositories=[world.gadget.repository_id], work_sources=["issues"])

    assert [(use.kind, use.id) for use in check.uses] == [
        ("repository", world.gadget.repository_id),
        ("work_source", world.issues.work_source_id),
    ]
    assert world.requests[0] == "GET /repos/acme/gadget"


def test_a_named_use_acting_with_another_credential_is_refused_naming_it(world: _World) -> None:
    with pytest.raises(CredentialNotUsedBy) as refused:
        world.readiness().check(world.deploy, repositories=["widget", "elsewhere", "nowhere"])

    assert refused.value.uses == (
        RecordRef(RecordKind.REPOSITORY, world.elsewhere.repository_id, "elsewhere"),
        RecordRef(RecordKind.REPOSITORY, "nowhere"),
    )
    assert refused.value.code == "credential_not_used_by"
    assert world.requests == [] and world.events() == []


def test_a_mint_refused_at_build_answers_every_use_reached_with_no_operations(world: _World) -> None:
    check = world.readiness(authorizations=_RefusingMint()).check(world.deploy)

    assert check.mint is not None and check.mint.outcome is CheckOutcome.REFUSED
    assert [(use.reached, use.operations) for use in check.uses] == [(True, ())] * 3
    assert world.requests == []


def test_an_installation_the_forge_does_not_answer_for_reaches_no_use_and_says_why(world: _World) -> None:
    check = world.readiness(authorizations=_Unanswered()).check(world.deploy)

    assert [use.reached for use in check.uses] == [False] * 3
    assert all(use.operations == () for use in check.uses)
    assert check.mint is None
    assert check.unreached_reason == "the installation could not be read: ReadTimeout"
    [(_kind, _severity, message, _detail)] = world.events()
    assert message.endswith("; 3 of 3 uses not reached (the installation could not be read: ReadTimeout)")
    assert world.requests == []


def test_the_authorization_s_build_is_bounded_by_what_the_check_has_left(world: _World) -> None:
    clock = ManualMonotonicClock()
    slow = _SlowBuild(world.live.authorizations, clock, seconds=45.0)

    check = world.readiness(authorizations=slow, monotonic=clock).check(world.deploy, repositories=["widget"])

    assert slow.bounds == [30.0, 15.0]
    assert check.uses[0].reached


def test_each_check_records_one_credential_check_event_with_its_counts(world: _World) -> None:
    check = world.readiness().check(world.deploy, repositories=["widget"])

    [(kind, severity, message, detail)] = world.events()
    counts = {"proven": 5, "granted": 3, "unproven": 0, "refused": 0, "unsupported": 0}
    assert (kind, severity) == ("credential-check", "info")
    assert json.loads(str(detail)) == {"credential_id": world.deploy.credential_id, "counts": counts}
    assert check.counts() == counts
    assert message == "credential deploy checked: 5 proven, 3 granted, 0 unproven, 0 refused, 0 unsupported"


def test_a_check_writes_nothing_but_its_event_and_sends_no_write(world: _World) -> None:
    before = world.config_rows()

    world.readiness().check(world.deploy)

    assert world.config_rows() == before
    assert all(request.startswith("GET ") for request in world.requests)
    assert "ghp_deploy" not in repr(world.events())
