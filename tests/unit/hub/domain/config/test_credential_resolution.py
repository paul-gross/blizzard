"""Which credential a repository or work-source write acts with, by value: every row of the old-field table, the four
refusals a record earns for the credential it names, an edit that would silently break the link, the slug a resolved
credential takes, and what a credential lets a landing policy do (unit tier)."""

from __future__ import annotations

from dataclasses import replace
from datetime import UTC, datetime

import pytest

from blizzard.foundation.landing_policy import LandingPolicy
from blizzard.hub.domain.config.capabilities import Capability
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, Door, RecordKind, RecordRef
from blizzard.hub.domain.config.credential_bindings import CREDENTIAL_BINDINGS
from blizzard.hub.domain.config.credential_naming import (
    CredentialBindingUnsupported,
    CredentialConflict,
    CredentialForgeMismatch,
    CredentialId,
    CredentialUnavailable,
    credential_lock,
)
from blizzard.hub.domain.config.credential_resolution import (
    CredentialCapabilityMissing,
    CredentialChoice,
    CredentialDecision,
    credential_offers,
    decide_credential,
    new_credential_slug,
    old_field_authority,
    old_field_draft,
    require_referrers_honoured,
)
from blizzard.hub.domain.config.credentials import (
    ConfiguredCredential,
    CredentialDraft,
)
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeFields, ForgeId, forge_lock
from blizzard.hub.domain.config.landing_policies import POLICY_CAPABILITIES
from blizzard.hub.domain.config.references import (
    NamedFacts,
    require_binding_usable,
    require_repository_references,
    require_work_source_references,
)
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryEdit,
    RepositoryFields,
    RepositoryId,
)
from blizzard.hub.domain.config.secrets import SecretMetadata, secret_lock
from blizzard.hub.domain.config.work_sources import (
    ConfiguredWorkSource,
    WorkSourceEdit,
    WorkSourceFields,
    WorkSourceId,
)
from blizzard.hub.domain.kernel.slugs import SLUG_MAX
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

_AT = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
_CTX = ChangeContext(actor="ops", door=Door.API)
_GHE, _OTHER = ForgeId("frg_ghe"), ForgeId("frg_other")
_ID = CredentialId("cred_deploy")


def _credential(
    *, secret: str = "gh", forge: ForgeId = _GHE, retired: bool = False, **settings: object
) -> ConfiguredCredential:
    draft = CredentialDraft("deploy", None, forge, "github.token", secret, settings)
    record = ConfiguredCredential.new(draft, CREDENTIAL_BINDINGS["github.token"], _CTX, at=_AT, credential_id=_ID)[0]
    return replace(record, retired=retired)


_CRED = _credential()


def _repository(
    *, credential: CredentialId | None = _ID, secret: str = "gh", retired: bool = False
) -> ConfiguredRepository:
    return ConfiguredRepository(
        repository_id=RepositoryId("repo_demo"),
        slug="demo",
        name="demo",
        fields=RepositoryFields(
            forge_id=_GHE, owner="acme", repo="demo", base_branch="main", secret_name=secret, credential_id=credential
        ),
        revision=2,
        created_at=_AT,
        created_by="op",
        retired=retired,
    )


def _source(*, credential: CredentialId | None = _ID, secret: str | None = "gh") -> ConfiguredWorkSource:
    return ConfiguredWorkSource(
        work_source_id=WorkSourceId("wsrc_demo"),
        slug="demo",
        name="demo",
        fields=WorkSourceFields(
            provider="github",
            locator="acme/demo",
            forge_id=_GHE,
            annotate=False,
            secret=secret,
            credential_id=credential,
        ),
        revision=2,
        created_at=_AT,
        created_by="op",
    )


_FORGE = ConfiguredForge(
    forge_id=_GHE,
    slug="ghe",
    name="ghe",
    kind="github",
    fields=ForgeFields("https://ghe.example/api", None),
    revision=1,
    created_at=_AT,
    created_by="op",
)
_SECRET = SecretMetadata("gh", 1, _AT, "op", _AT, retired=False)


def _facts(*credentials: ConfiguredCredential) -> NamedFacts:
    return NamedFacts(
        forges={_GHE: _FORGE},
        secrets={"gh": _SECRET},
        credentials={credential.credential_id: credential for credential in credentials},
    )


# --- The old-field table ---------------------------------------------------------------------------------------------


def _decide(
    choice: CredentialChoice,
    old: str | None | UnsetType,
    *,
    current: str | None | UnsetType,
    required: bool = True,
) -> CredentialDecision:
    return decide_credential(choice, old, current=current, field="secret_name", required=required)


_NAMED = CredentialChoice(credential="deploy", named=_CRED)


def test_row_one_a_credential_alone_is_used() -> None:
    assert _decide(_NAMED, UNSET, current=UNSET) == CredentialDecision(use=_CRED)
    assert _decide(_NAMED, UNSET, current="other") == CredentialDecision(use=_CRED)


def test_row_two_a_credential_beside_an_agreeing_old_field_is_used() -> None:
    assert _decide(_NAMED, "gh", current=UNSET) == CredentialDecision(use=_CRED)


def test_row_three_an_old_field_equal_to_the_record_s_changes_nothing() -> None:
    assert _decide(CredentialChoice(), "gh", current="gh") == CredentialDecision()


def test_an_old_field_equal_to_the_record_s_resolves_on_the_forge_an_edit_moves_it_to() -> None:
    decided = decide_credential(
        CredentialChoice(), "gh", current="gh", field="secret_name", required=False, forge_moved=True
    )
    assert decided == CredentialDecision(resolve="gh")


def test_row_four_any_other_old_field_alone_resolves_its_secret() -> None:
    assert _decide(CredentialChoice(), "gh", current=UNSET) == CredentialDecision(resolve="gh")
    assert _decide(CredentialChoice(), "gh2", current="gh") == CredentialDecision(resolve="gh2")


def test_neither_field_leaves_an_edit_alone_and_refuses_a_create_that_needs_one() -> None:
    assert _decide(CredentialChoice(), UNSET, current="gh") == CredentialDecision()
    assert _decide(CredentialChoice(), UNSET, current=UNSET, required=False) == CredentialDecision()
    with pytest.raises(ConfigFieldError) as caught:
        _decide(CredentialChoice(), UNSET, current=UNSET)
    assert (caught.value.code, caught.value.field) == ("value_error", "credential")


def test_a_null_old_field_on_an_edit_is_left_to_the_record() -> None:
    assert _decide(CredentialChoice(), None, current="gh", required=False) == CredentialDecision()


@pytest.mark.parametrize(
    ("named", "retired"), [(None, False), (_credential(retired=True), True)], ids=["unknown", "retired"]
)
def test_a_credential_unknown_or_retired_is_unavailable(named: ConfiguredCredential | None, retired: bool) -> None:
    with pytest.raises(CredentialUnavailable) as caught:
        _decide(CredentialChoice(credential="deploy", named=named), UNSET, current=UNSET)
    assert (caught.value.code, caught.value.field, caught.value.retired) == (
        "credential_unavailable",
        "credential",
        retired,
    )


def test_an_old_field_disagreeing_with_the_credential_conflicts() -> None:
    with pytest.raises(CredentialConflict) as caught:
        _decide(_NAMED, "gh2", current=UNSET)
    assert (caught.value.code, caught.value.field) == ("credential_conflict", "secret_name")


def test_an_old_field_beside_a_credential_of_another_binding_conflicts() -> None:
    other = replace(_CRED, fields=replace(_CRED.fields, binding="github.app-installation"))
    with pytest.raises(CredentialConflict):
        _decide(CredentialChoice(credential="deploy", named=other), "gh", current=UNSET)


def test_an_old_field_resolves_to_a_github_token_credential_named_for_its_secret() -> None:
    assert old_field_authority("gh") == "secret/gh"
    assert old_field_draft(_GHE, "gh", slug="gh-2") == CredentialDraft("gh-2", "gh", _GHE, "github.token", "gh", {})


# --- The slug rule ---------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("secret", "taken", "slug"),
    [
        ("gh", set[str](), "gh"),
        ("gh", {"gh"}, "gh-2"),
        ("gh", {"gh", "gh-2"}, "gh-3"),
        ("gh", {"gh-2"}, "gh"),
        ("-gh--token-", set[str](), "gh-token"),
        ("---", set[str](), "credential"),
    ],
)
def test_a_resolved_credential_takes_its_secret_s_name_then_the_next_free_suffix(
    secret: str, taken: set[str], slug: str
) -> None:
    assert new_credential_slug(secret, taken) == slug


def test_a_suffixed_slug_stays_within_the_slug_length() -> None:
    long = "a" * SLUG_MAX
    slug = new_credential_slug(long, {long})
    assert (len(slug), slug.endswith("-2")) == (SLUG_MAX, True)


# --- The referrer refusals -------------------------------------------------------------------------------------------


def test_a_record_naming_a_usable_credential_on_its_forge_and_secret_is_not_refused() -> None:
    require_repository_references(_repository(), ChangeOp.EDIT, _facts(_CRED))
    require_work_source_references(_source(), ChangeOp.EDIT, _facts(_CRED))
    require_repository_references(_repository(credential=None), ChangeOp.EDIT, _facts())


def test_a_record_naming_an_unknown_credential_is_unavailable() -> None:
    with pytest.raises(CredentialUnavailable, match="credential cred_deploy is unknown"):
        require_repository_references(_repository(), ChangeOp.CREATE, _facts())


def test_an_enabled_record_naming_a_retired_credential_is_unavailable_and_a_retired_one_keeps_it() -> None:
    retired = _credential(retired=True)
    with pytest.raises(CredentialUnavailable, match="credential deploy is retired"):
        require_work_source_references(_source(), ChangeOp.EDIT, _facts(retired))
    require_repository_references(_repository(retired=True), ChangeOp.EDIT, _facts(retired))
    with pytest.raises(CredentialUnavailable):
        require_repository_references(_repository(retired=True), ChangeOp.ENABLE, _facts(retired))


def test_a_record_naming_a_credential_of_another_forge_is_a_forge_mismatch() -> None:
    with pytest.raises(CredentialForgeMismatch) as caught:
        require_repository_references(_repository(), ChangeOp.EDIT, _facts(_credential(forge=_OTHER)))
    assert caught.value.code == "credential_forge_mismatch"


def test_a_record_whose_secret_is_not_its_credential_s_conflicts() -> None:
    with pytest.raises(CredentialConflict, match="reads secret gh2, not gh"):
        require_repository_references(_repository(), ChangeOp.EDIT, _facts(_credential(secret="gh2")))
    with pytest.raises(CredentialConflict):
        require_work_source_references(_source(secret=None), ChangeOp.EDIT, _facts(_CRED))


def test_a_provider_acts_only_with_the_bindings_its_table_lists() -> None:
    require_binding_usable("github", _CRED)
    with pytest.raises(CredentialBindingUnsupported) as caught:
        require_binding_usable("github", _CRED, usable={"github": frozenset({"github.app-installation"})})
    assert caught.value.code == "credential_binding_unsupported"
    with pytest.raises(CredentialBindingUnsupported):
        require_binding_usable("gitlab", _CRED)


def test_a_retire_names_nothing_and_is_never_refused_for_its_credential() -> None:
    require_repository_references(_repository(), ChangeOp.RETIRE, _facts())


# --- An edit can't silently break the link ---------------------------------------------------------------------------


def test_an_edit_moving_a_linked_record_s_forge_without_naming_a_credential_is_a_forge_mismatch() -> None:
    with pytest.raises(CredentialForgeMismatch):
        _repository().edit(RepositoryEdit(forge_id=_OTHER), _CTX, if_match=None, at=_AT)
    with pytest.raises(CredentialForgeMismatch):
        _source().edit(WorkSourceEdit(forge_id=_OTHER), _CTX, if_match=None, at=_AT)


def test_an_edit_moving_a_linked_record_s_secret_without_naming_a_credential_conflicts() -> None:
    with pytest.raises(CredentialConflict) as caught:
        _repository().edit(RepositoryEdit(secret_name="gh2"), _CTX, if_match=None, at=_AT)
    assert caught.value.field == "secret_name"
    with pytest.raises(CredentialConflict):
        _source().edit(WorkSourceEdit(secret=None), _CTX, if_match=None, at=_AT)


def test_an_edit_naming_a_credential_or_of_an_unlinked_record_moves_freely() -> None:
    moved = _repository().edit(
        RepositoryEdit(secret_name="gh2", credential_id=CredentialId("cred_other")), _CTX, if_match=None, at=_AT
    )
    assert moved is not None
    assert [(change.field, change.old, change.new) for change in moved[1].diff] == [
        ("secret_name", "gh", "gh2"),
        ("credential", "cred_deploy", "cred_other"),
    ]
    assert _repository(credential=None).edit(RepositoryEdit(secret_name="gh2"), _CTX, if_match=None, at=_AT)


def test_an_edit_never_unlinks_a_repository_from_its_credential() -> None:
    with pytest.raises(ConfigFieldError, match="must not be null") as caught:
        _repository().edit(RepositoryEdit(credential_id=None), _CTX, if_match=None, at=_AT)
    assert caught.value.field == "credential"


def test_a_record_naming_a_credential_declares_its_lock() -> None:
    assert _repository().locks() == [forge_lock(_GHE), secret_lock("gh"), credential_lock(_ID)]
    assert _source().locks() == [forge_lock(_GHE), secret_lock("gh"), credential_lock(_ID)]
    assert _repository(credential=None).locks() == [forge_lock(_GHE), secret_lock("gh")]


# --- What a credential lets a landing policy do ----------------------------------------------------------------------

_KIND = frozenset(Capability)


def test_a_classic_or_undeclared_token_offers_its_forge_s_kind_and_a_fine_grained_one_withholds_checks() -> None:
    assert credential_offers(_credential(variant="classic"), _KIND) == _KIND
    assert credential_offers(_CRED, _KIND) == _KIND
    assert credential_offers(_credential(variant="fine-grained"), _KIND) == _KIND - {Capability.CHECKS_READ}


def test_a_fine_grained_token_behind_merge_on_checks_is_refused_naming_the_repository() -> None:
    repository = _repository()
    assert repository.fields.landing_policy is LandingPolicy.MERGE_ON_CHECKS
    require_referrers_honoured(_credential(variant="classic"), [(repository, _KIND)])
    with pytest.raises(CredentialCapabilityMissing) as caught:
        require_referrers_honoured(_credential(variant="fine-grained"), [(repository, _KIND)])
    assert caught.value.code == "forge_capability_missing"
    assert caught.value.referrers == [RecordRef(RecordKind.REPOSITORY, "repo_demo", "demo")]
    assert caught.value.missing == (Capability.CHECKS_READ,)


def test_a_fine_grained_token_behind_a_policy_reading_no_checks_or_a_retired_repository_is_honoured() -> None:
    parked = replace(_repository(), fields=replace(_repository().fields, landing_policy=LandingPolicy.PARK))
    assert Capability.CHECKS_READ not in POLICY_CAPABILITIES[LandingPolicy.PARK].requires
    fine_grained = _credential(variant="fine-grained")
    require_referrers_honoured(fine_grained, [(parked, _KIND), (_repository(retired=True), _KIND)])
