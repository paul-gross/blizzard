"""Configured-record lifecycle decisions by value: work source, repository, secret, and door (unit tier)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from blizzard.hub.domain.config import secret_lifecycle
from blizzard.hub.domain.config.changes import (
    FIELDED_RECORD_TRANSITIONS,
    SECRET_TRANSITIONS,
    ChangeContext,
    ChangeOp,
    ConfigChange,
    Door,
    FieldChange,
    RecordKind,
    RecordState,
    Verdict,
)
from blizzard.hub.domain.config.repositories import ConfiguredRepository, RepositoryEdit, RepositoryFields
from blizzard.hub.domain.config.secrets import SecretMetadata, SecretName, SecretRetired, SecretRevisionConflict
from blizzard.hub.domain.config.work_sources import (
    BuiltInWorkSource,
    ConfigFieldError,
    ConfigRevisionConflict,
    ConfiguredWorkSource,
    WorkSourceEdit,
    WorkSourceFields,
    is_built_in,
    require_configurable,
)

pytestmark = pytest.mark.unit

_AT = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
_CTX = ChangeContext(actor="ops", door=Door.CLI)
_WS_FIELDS = WorkSourceFields(
    provider="github", locator="acme/demo", api_base=None, web_base=None, annotate=False, secret="gh"
)
_REPO_FIELDS = RepositoryFields(
    forge_api_url="https://api.github.com", owner="acme", repo="demo", base_branch="master", secret_name="gh"
)


def _source(*, revision: int = 3, retired: bool = False) -> ConfiguredWorkSource:
    return ConfiguredWorkSource(
        name="gh", fields=_WS_FIELDS, revision=revision, created_at=_AT, created_by="ops", retired=retired
    )


def _repo(*, revision: int = 3, retired: bool = False) -> ConfiguredRepository:
    return ConfiguredRepository(
        name="demo", fields=_REPO_FIELDS, revision=revision, created_at=_AT, created_by="ops", retired=retired
    )


def _secret(*, revision: int = 2) -> SecretMetadata:
    return SecretMetadata(name="gh", revision=revision, replaced_at=_AT, replaced_by="ops", created_at=_AT)


def _change(kind: RecordKind, key: str, revision: int, op: ChangeOp, diff: tuple[FieldChange, ...]) -> ConfigChange:
    return ConfigChange(
        recorded_at=_AT,
        actor="ops",
        door=Door.CLI,
        record_kind=kind,
        record_key=key,
        revision=revision,
        op=op,
        diff=diff,
    )


# --- Transition tables ------------------------------------------------------------


def test_a_fielded_record_is_editable_from_either_state_and_a_redundant_brake_is_a_no_op() -> None:
    assert FIELDED_RECORD_TRANSITIONS == {
        RecordState.ACTIVE: {
            ChangeOp.EDIT: Verdict.LEGAL,
            ChangeOp.RETIRE: Verdict.LEGAL,
            ChangeOp.ENABLE: Verdict.NO_OP,
        },
        RecordState.RETIRED: {
            ChangeOp.EDIT: Verdict.LEGAL,
            ChangeOp.RETIRE: Verdict.NO_OP,
            ChangeOp.ENABLE: Verdict.LEGAL,
        },
    }
    assert ConfiguredWorkSource.TRANSITIONS is FIELDED_RECORD_TRANSITIONS
    assert ConfiguredRepository.TRANSITIONS is FIELDED_RECORD_TRANSITIONS


def test_a_retired_secret_refuses_a_replacement_and_a_redundant_brake_is_a_no_op() -> None:
    assert SECRET_TRANSITIONS == {
        RecordState.ACTIVE: {
            ChangeOp.REPLACE: Verdict.LEGAL,
            ChangeOp.RETIRE: Verdict.LEGAL,
            ChangeOp.ENABLE: Verdict.NO_OP,
        },
        RecordState.RETIRED: {
            ChangeOp.REPLACE: Verdict.REFUSED,
            ChangeOp.RETIRE: Verdict.NO_OP,
            ChangeOp.ENABLE: Verdict.LEGAL,
        },
    }


# --- Work source ------------------------------------------------------------------


def test_a_new_work_source_starts_at_revision_one_with_a_create_change_listing_every_set_field() -> None:
    record, change = ConfiguredWorkSource.new("gh", _WS_FIELDS, _CTX, at=_AT)

    assert record == ConfiguredWorkSource(name="gh", fields=_WS_FIELDS, revision=1, created_at=_AT, created_by="ops")
    assert (change.record_kind, change.record_key, change.revision, change.op) == (
        RecordKind.WORK_SOURCE,
        "gh",
        1,
        ChangeOp.CREATE,
    )
    assert [c.field for c in change.diff] == ["provider", "locator", "annotate", "secret"]


def test_a_new_work_source_with_a_bad_name_is_refused() -> None:
    with pytest.raises(ConfigFieldError):
        ConfiguredWorkSource.new("hub", _WS_FIELDS, _CTX, at=_AT)


def test_a_work_source_edit_moves_the_revision_and_records_the_diff() -> None:
    decided = _source().edit(WorkSourceEdit(annotate=True), _CTX, if_match=3, at=_AT)

    assert decided == (
        ConfiguredWorkSource(
            name="gh",
            fields=WorkSourceFields(
                provider="github", locator="acme/demo", api_base=None, web_base=None, annotate=True, secret="gh"
            ),
            revision=4,
            created_at=_AT,
            created_by="ops",
        ),
        _change(RecordKind.WORK_SOURCE, "gh", 4, ChangeOp.EDIT, (FieldChange("annotate", False, True),)),
    )


def test_a_work_source_edit_that_changes_nothing_writes_nothing() -> None:
    assert _source().edit(WorkSourceEdit(annotate=False), _CTX, if_match=None, at=_AT) is None


def test_a_stale_if_match_is_refused_even_for_an_edit_that_changes_nothing() -> None:
    with pytest.raises(ConfigRevisionConflict) as caught:
        _source().edit(WorkSourceEdit(), _CTX, if_match=2, at=_AT)
    assert str(caught.value) == ConfigRevisionConflict("work source", "gh", current=3).args[0]


def test_an_edit_that_changes_nothing_never_revalidates_the_stored_fields() -> None:
    stored = ConfiguredWorkSource(
        name="gh",
        fields=WorkSourceFields(
            provider="nowhere", locator="x", api_base=None, web_base=None, annotate=False, secret=None
        ),
        revision=1,
        created_at=_AT,
        created_by="ops",
    )
    assert stored.edit(WorkSourceEdit(), _CTX, if_match=None, at=_AT) is None


def test_a_retired_work_source_is_edited_and_stays_retired() -> None:
    decided = _source(retired=True).edit(WorkSourceEdit(annotate=True), _CTX, if_match=None, at=_AT)

    assert decided is not None
    assert decided[0].retired is True
    assert decided[0].revision == 4


def test_retiring_an_active_work_source_flips_it_at_the_next_revision() -> None:
    decided = _source().set_retired(True, _CTX, if_match=3, at=_AT)

    assert decided == (
        _source(revision=4, retired=True),
        _change(RecordKind.WORK_SOURCE, "gh", 4, ChangeOp.RETIRE, (FieldChange("retired", False, True),)),
    )


def test_enabling_a_retired_work_source_flips_it_back() -> None:
    decided = _source(retired=True).set_retired(False, _CTX, if_match=None, at=_AT)

    assert decided == (
        _source(revision=4),
        _change(RecordKind.WORK_SOURCE, "gh", 4, ChangeOp.ENABLE, (FieldChange("retired", True, False),)),
    )


@pytest.mark.parametrize("retired", [True, False])
def test_a_redundant_retire_or_enable_of_a_work_source_writes_nothing(retired: bool) -> None:
    assert _source(retired=retired).set_retired(retired, _CTX, if_match=None, at=_AT) is None


def test_a_stale_if_match_is_refused_before_a_redundant_retire_is_a_no_op() -> None:
    with pytest.raises(ConfigRevisionConflict):
        _source(retired=True).set_retired(True, _CTX, if_match=1, at=_AT)


def test_only_the_built_in_hub_source_is_built_in() -> None:
    assert is_built_in("hub") is True
    assert is_built_in("gh") is False
    require_configurable("gh")
    with pytest.raises(BuiltInWorkSource) as caught:
        require_configurable("hub")
    assert str(caught.value) == "work source hub is built in and cannot be changed"


# --- Repository -------------------------------------------------------------------


def test_a_new_repository_starts_at_revision_one_with_a_create_change() -> None:
    record, change = ConfiguredRepository.new("demo", _REPO_FIELDS, _CTX, at=_AT)

    assert record.revision == 1 and record.created_by == "ops" and not record.retired
    assert (change.record_kind, change.revision, change.op) == (RecordKind.REPOSITORY, 1, ChangeOp.CREATE)


def test_a_repository_edit_moves_the_revision_and_an_empty_one_writes_nothing() -> None:
    decided = _repo().edit(RepositoryEdit(base_branch="main"), _CTX, if_match=3, at=_AT)

    assert decided is not None
    assert decided[0].revision == 4 and decided[0].fields.base_branch == "main"
    assert decided[1] == _change(
        RecordKind.REPOSITORY, "demo", 4, ChangeOp.EDIT, (FieldChange("base_branch", "master", "main"),)
    )
    assert _repo().edit(RepositoryEdit(), _CTX, if_match=3, at=_AT) is None


def test_a_stale_if_match_on_a_repository_names_the_repository_kind() -> None:
    with pytest.raises(ConfigRevisionConflict) as caught:
        _repo().set_retired(True, _CTX, if_match=9, at=_AT)
    assert str(caught.value) == str(ConfigRevisionConflict("repository", "demo", current=3))


def test_a_repository_retires_once_and_a_redundant_retire_writes_nothing() -> None:
    decided = _repo().set_retired(True, _CTX, if_match=None, at=_AT)

    assert decided == (
        _repo(revision=4, retired=True),
        _change(RecordKind.REPOSITORY, "demo", 4, ChangeOp.RETIRE, (FieldChange("retired", False, True),)),
    )
    assert _repo(retired=True).set_retired(True, _CTX, if_match=None, at=_AT) is None


# --- Secret -----------------------------------------------------------------------


def test_a_new_secret_writes_a_create_change_at_revision_one_with_no_diff() -> None:
    assert secret_lifecycle.creation(SecretName.parse("gh"), _CTX, at=_AT) == _change(
        RecordKind.SECRET, "gh", 1, ChangeOp.CREATE, ()
    )


def test_replacing_an_active_secret_writes_at_the_next_revision() -> None:
    change = secret_lifecycle.replacement(_secret(), _CTX, retired=False, if_match=2, at=_AT)

    assert change == _change(RecordKind.SECRET, "gh", 3, ChangeOp.REPLACE, ())


def test_replacing_a_retired_secret_is_refused_before_a_stale_if_match() -> None:
    with pytest.raises(SecretRetired):
        secret_lifecycle.replacement(_secret(), _CTX, retired=True, if_match=9, at=_AT)


def test_replacing_with_a_stale_if_match_is_refused() -> None:
    with pytest.raises(SecretRevisionConflict):
        secret_lifecycle.replacement(_secret(), _CTX, retired=False, if_match=1, at=_AT)


def test_a_secret_retire_writes_at_the_unchanged_revision() -> None:
    change = secret_lifecycle.lifecycle_change(_secret(), _CTX, retired_now=False, retired=True, at=_AT)

    assert change == _change(RecordKind.SECRET, "gh", 2, ChangeOp.RETIRE, (FieldChange("retired", False, True),))


def test_a_secret_enable_writes_at_the_unchanged_revision() -> None:
    change = secret_lifecycle.lifecycle_change(_secret(), _CTX, retired_now=True, retired=False, at=_AT)

    assert change == _change(RecordKind.SECRET, "gh", 2, ChangeOp.ENABLE, (FieldChange("retired", True, False),))


@pytest.mark.parametrize("retired", [True, False])
def test_a_redundant_secret_retire_or_enable_writes_nothing(retired: bool) -> None:
    assert secret_lifecycle.lifecycle_change(_secret(), _CTX, retired_now=retired, retired=retired, at=_AT) is None


# --- Door -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "door"),
    [
        ("cli", Door.CLI),
        ("board", Door.BOARD),
        ("apply", Door.API),
        ("migration", Door.API),
        ("x", Door.API),
        (None, Door.API),
    ],
)
def test_a_client_may_claim_only_the_cli_or_board_door(raw: str | None, door: Door) -> None:
    assert Door.claimed(raw) is door
