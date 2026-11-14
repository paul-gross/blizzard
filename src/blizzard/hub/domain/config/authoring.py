"""``ConfigAuthoring`` — the one writer of configured records.

Every verb takes the record the edge resolved (``bzh:domain-takes-objects``), asks the record's model for the change,
and hands it to the write repository with the record write. A write that changes nothing writes nothing. Secrets,
forges and credentials route through here too.

A repository or work-source write from a door goes through the ``…_naming`` verbs, which take the forge the write names
as the edge resolved it: the forge is decided first
(:func:`~blizzard.hub.domain.config.forge_resolution.resolve_forge`), then the record is built and validated on it.

A door's write names its credential as a :class:`~blizzard.hub.domain.config.credential_resolution.CredentialChoice`
beside the old secret field, decided by the old-field table
(:func:`~blizzard.hub.domain.config.credential_resolution.decide_credential`); an old field alone resolves, inside the
record's unit, to the ``github.token`` credential reading it on the record's forge, created when there is none
(:meth:`ConfigAuthoring.credential_for_old_field`).

Every repository write that sets a landing policy — a create, a policy edit, a forge or credential change, and each
repository write an apply or an import plans — is checked against what :class:`IForgeOffers` says its forge offers,
narrowed by the declared offers of the credential it acts with, and refused
:class:`~blizzard.hub.domain.config.landing_policies.ForgeCapabilityMissing` before anything is written. A per-record
write judges it inside its unit, on the forge and credential read there."""

from __future__ import annotations

from collections.abc import Callable, Collection, Generator, Iterable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, replace
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.foundation.secrets.sealing import ISecretCipher
from blizzard.foundation.secrets.value import SecretValue
from blizzard.hub.domain.config import secret_lifecycle
from blizzard.hub.domain.config.apply import (
    ApplyEntryRefused,
    ApplyPlan,
    ConfigDeclaration,
    DeclaredOldField,
    EntryOutcome,
    IConfigApplyWriter,
    PlannedWrite,
    StoredConfig,
    reconcile,
)
from blizzard.hub.domain.config.capabilities import IForgeOffers
from blizzard.hub.domain.config.carry_over import (
    MIGRATION_CONTEXT,
    CommitCoordinate,
    ExistingConfig,
    IConfigImportWriter,
    IImportLinks,
    ImportResult,
    ImportStatus,
    LegacyImport,
    SealedSecretWrite,
    plan_import,
)
from blizzard.hub.domain.config.changes import ChangeContext, ChangeOp, ISecretReferences
from blizzard.hub.domain.config.credential_naming import CredentialId, CredentialUnavailable, credential_lock
from blizzard.hub.domain.config.credential_resolution import (
    OLD_FIELD_BINDING,
    CredentialChoice,
    CredentialDecision,
    credential_offers,
    decide_credential,
    old_field_authority,
    require_referrers_honoured,
    resolve_old_field,
)
from blizzard.hub.domain.config.credentials import (
    ConfiguredCredential,
    CredentialAuthorityTaken,
    CredentialDraft,
    CredentialEdit,
    CredentialSecretUnavailable,
    CredentialSlugTaken,
    ICredentialReferences,
    IWriteCredentialRepository,
    binding_named,
    require_credential_references,
)
from blizzard.hub.domain.config.credentials import require_unused as require_credential_unused
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forge_resolution import ForgeChoice, resolve_forge
from blizzard.hub.domain.config.forges import (
    ConfiguredForge,
    ForgeEdit,
    ForgeFields,
    ForgeId,
    IForgeReferences,
    IWriteForgeRepository,
    forge_lock,
    require_unused,
)
from blizzard.hub.domain.config.landing_policies import require_offered
from blizzard.hub.domain.config.legacy_keys import LegacyKeys
from blizzard.hub.domain.config.references import (
    NamedFacts,
    references_refusal,
    require_repository_references,
    require_work_source_references,
)
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    IWriteRepositoryRecordRepository,
    RepositoryDraft,
    RepositoryEdit,
    RepositoryFields,
    RepositorySecretUnavailable,
)
from blizzard.hub.domain.config.secrets import (
    IWriteSecretRepository,
    SecretMetadata,
    SecretName,
    require_unreferenced,
    secret_lock,
)
from blizzard.hub.domain.config.work_sources import (
    ConfiguredWorkSource,
    IWriteWorkSourceRepository,
    WorkSourceDraft,
    WorkSourceEdit,
    WorkSourceFields,
    WorkSourceSecretUnavailable,
    require_known_provider,
)
from blizzard.hub.domain.kernel.locks import LockKey, LockNamespace
from blizzard.hub.domain.kernel.transactions import IHubTransactions, LockedTransaction
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType


class _Rollback(Exception):
    """Raised out of a unit that must leave nothing behind — a dry run, or an import recorded first."""


class _Reresolve(Exception):
    """Raised out of a unit whose old field resolved to a credential other than the one it locked — one another write
    created first — so the write runs again locking that one."""

    def __init__(self, credential_id: CredentialId) -> None:
        super().__init__(credential_id)
        self.credential_id = credential_id


class _ApplyReresolve(Exception):
    """Raised out of an apply's unit whose old field resolved to ``credential_id`` rather than ``planned``, the one the
    apply planned and locked — one another write created first — so the apply runs again naming that one."""

    def __init__(self, planned: CredentialId, credential_id: CredentialId) -> None:
        super().__init__(planned, credential_id)
        self.planned = planned
        self.credential_id = credential_id


@domain_model
@dataclass(frozen=True)
class OldFieldUse:
    """An old secret field a write sent alone, and the credential it resolved to."""

    field: str
    credential: ConfiguredCredential


@domain_model
@dataclass(frozen=True)
class Authored[R]:
    """A door's write: the record written, and each old field it resolved to a credential."""

    record: R
    old_fields: tuple[OldFieldUse, ...] = ()


@domain_model
@dataclass(frozen=True)
class _OldField:
    """An old field to resolve inside a referrer's unit: its wire name, the forge and secret it names, and the
    credential the unit locks for it."""

    field: str
    forge_id: ForgeId
    secret_name: str
    planned: CredentialId

    def secret_refusal(self, refused: CredentialSecretUnavailable) -> ConfigFieldError:
        """``refused`` — the credential's refusal of the secret — as the referrer's own refusal of its old field."""
        if self.field == "secret_name":
            return RepositorySecretUnavailable(refused.secret, retired=refused.retired)
        return WorkSourceSecretUnavailable(refused.secret, retired=refused.retired)


#: A write that names no credential and sends no old field.
_NO_CREDENTIAL = CredentialChoice()

#: Whether a repository unit judges what its forge offers the record's policy: for a record it creates (asked of the
#: forge's kind), for one it edits (asked of the repository), or not at all.
type _OffersCheck = bool | None
_CREATED: _OffersCheck = True
_EDITED: _OffersCheck = False
_UNCHECKED: _OffersCheck = None


def _named_credential(decision: CredentialDecision, old: _OldField | None) -> tuple[CredentialId, str] | None:
    """The credential a write acts with and the secret it mirrors: the one ``decision`` names, else the one ``old``
    resolves to — ``None`` when the write leaves its credential as stored."""
    if decision.use is not None:
        return decision.use.credential_id, decision.use.fields.secret_name
    if old is not None:
        return old.planned, old.secret_name
    return None


def _used(old: _OldField | None, resolved: ConfiguredCredential | None) -> tuple[OldFieldUse, ...]:
    """The old field a write resolved, with the credential it resolved to — none when it resolved no old field."""
    if old is None or resolved is None:
        return ()
    return (OldFieldUse(old.field, resolved),)


class ConfigAuthoring:
    def __init__(
        self,
        *,
        work_sources: IWriteWorkSourceRepository,
        repositories: IWriteRepositoryRecordRepository,
        forges: IWriteForgeRepository,
        credentials: IWriteCredentialRepository,
        offers: IForgeOffers,
        secrets: IWriteSecretRepository,
        cipher: ISecretCipher,
        apply_writer: IConfigApplyWriter,
        import_writer: IConfigImportWriter,
        transactions: IHubTransactions,
        secret_references: ISecretReferences,
        forge_references: IForgeReferences,
        credential_references: ICredentialReferences,
        clock: IClock,
    ) -> None:
        """``offers`` answers what a repository's forge offers its landing policy; ``transactions`` opens the unit
        each write runs in, and ``secret_references``, ``forge_references`` and ``credential_references`` read, in it,
        the records naming a secret, a forge or a credential being retired."""
        self._work_sources = work_sources
        self._repositories = repositories
        self._forges = forges
        self._credentials = credentials
        self._offers = offers
        self._secrets = secrets
        self._cipher = cipher
        self._apply_writer = apply_writer
        self._import_writer = import_writer
        self._transactions = transactions
        self._secret_references = secret_references
        self._forge_references = forge_references
        self._credential_references = credential_references
        self._clock = clock

    # --- Forges ------------------------------------------------------------------

    def create_forge(
        self, slug: str, name: str | None, kind: str, fields: ForgeFields, ctx: ChangeContext
    ) -> ConfiguredForge:
        record, change = ConfiguredForge.new(slug, name, kind, fields, ctx, at=self._clock.now())
        return self._forges.create(record, change=change)

    def edit_forge(
        self, record: ConfiguredForge, edit: ForgeEdit, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredForge:
        """Edit the forge; the addresses every record naming it holds move with it in the same write."""
        decided = record.edit(edit, ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return record
        edited, change = decided
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=[forge_lock(record.forge_id)])
            return self._forges.update(
                edited,
                from_revision=record.revision,
                left_slug=record.slug_left_for(edited),
                change=change,
                transaction=locked,
            )

    def retire_forge(
        self, record: ConfiguredForge, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredForge:
        """Retire the forge. :class:`~blizzard.hub.domain.config.forges.ForgeInUse` while an enabled record names it."""
        return self._set_forge_retired(record, True, ctx, if_match)

    def enable_forge(
        self, record: ConfiguredForge, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredForge:
        return self._set_forge_retired(record, False, ctx, if_match)

    def _set_forge_retired(
        self, record: ConfiguredForge, retired: bool, ctx: ChangeContext, if_match: int | None
    ) -> ConfiguredForge:
        now = self._clock.now()
        decided = record.set_retired(retired, ctx, if_match=if_match, at=now)
        if decided is None:
            return record
        moved, change = decided
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=[forge_lock(record.forge_id)])
            if retired:
                referrers = self._forge_references.referrers_of([record.forge_id], transaction=locked)
                require_unused(moved.slug, referrers[record.forge_id])
            return self._forges.record_lifecycle(
                moved,
                retired=retired,
                from_revision=record.revision,
                at=now,
                by=ctx.actor,
                change=change,
                transaction=locked,
            )

    def _resolve(self, choice: ForgeChoice, *, provider: str | None, current: ForgeId | None) -> ConfiguredForge | None:
        """:func:`resolve_forge` against the tenant's forges, for a create when ``current`` is ``None``."""
        return resolve_forge(
            choice,
            provider=provider,
            current=None if current is None else self._forges.get(current),
            creating=current is None,
            forges=lambda: self._forges.list_all(include_retired=True),
        )

    # --- Credentials -------------------------------------------------------------

    def create_credential(self, draft: CredentialDraft, ctx: ChangeContext) -> ConfiguredCredential:
        """Create a credential on the forge ``draft`` names, under the binding it names.
        :class:`~blizzard.hub.domain.config.credentials.CredentialBindingUnknown`, the binding's settings refusal, and
        the refusals of :meth:`_credential_unit`."""
        binding = binding_named(draft.binding)
        record, change = ConfiguredCredential.new(draft, binding, ctx, at=self._clock.now())
        with self._credential_unit(record, change.op, held=[]) as (locked, _):
            return self._credentials.create(record, change=change, transaction=locked)

    def edit_credential(
        self, record: ConfiguredCredential, edit: CredentialEdit, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredCredential:
        """Edit the credential; its forge and binding never change, and its authority is derived again.

        An edit of its settings is refused
        :class:`~blizzard.hub.domain.config.credential_resolution.CredentialCapabilityMissing` when what it would offer
        falls short of an enabled repository acting with it; an edit of its secret rewrites every record acting with it
        to the new one (``hub:credential-secret-mirrored``), each at its own revision with its own change row."""
        now = self._clock.now()
        decided = record.edit(edit, binding_named(record.fields.binding), ctx, if_match=if_match, at=now)
        if decided is None:
            return record
        edited, change = decided
        credential_id = record.credential_id
        with self._credential_unit(edited, change.op, held=[credential_lock(credential_id)]) as (locked, facts):
            if edited.fields.settings != record.fields.settings:
                forge = facts.forges[edited.fields.forge_id]
                repositories = self._repositories.naming_credential(credential_id, transaction=locked)
                require_referrers_honoured(
                    edited, [(repo, self._offers.offered(forge, repo.repository_id)) for repo in repositories]
                )
            written = self._credentials.update(
                edited,
                from_revision=record.revision,
                left_slug=record.slug_left_for(edited),
                change=change,
                transaction=locked,
            )
            if edited.fields.secret_name != record.fields.secret_name:
                self._mirror_secret(written, ctx, locked, at=now)
            return written

    def _mirror_secret(
        self,
        credential: ConfiguredCredential,
        ctx: ChangeContext,
        locked: LockedTransaction,
        *,
        at: datetime,
        skip: Collection[str] = (),
    ) -> None:
        """Rewrite the old secret field of every record acting with ``credential`` — retired ones too — to its secret,
        in ``locked``, which holds the credential's keys and so every key such a record names; a record whose id is in
        ``skip`` writes its own."""
        credential_id, secret = credential.credential_id, credential.fields.secret_name
        for repository in self._repositories.naming_credential(credential_id, transaction=locked):
            if repository.repository_id in skip:
                continue
            decided = repository.edit(
                RepositoryEdit(secret_name=secret, credential_id=credential_id), ctx, if_match=None, at=at
            )
            if decided is not None:
                self._repositories.update(
                    decided[0], from_revision=repository.revision, left_slug=None, change=decided[1], transaction=locked
                )
        for source in self._work_sources.naming_credential(credential_id, transaction=locked):
            if source.work_source_id in skip:
                continue
            moved = source.edit(WorkSourceEdit(secret=secret, credential_id=credential_id), ctx, if_match=None, at=at)
            if moved is not None:
                self._work_sources.update(
                    moved[0], from_revision=source.revision, left_slug=None, change=moved[1], transaction=locked
                )

    def retire_credential(
        self, record: ConfiguredCredential, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredCredential:
        """Retire the credential. :class:`~blizzard.hub.domain.config.credentials.CredentialInUse` while an enabled
        record names it."""
        now = self._clock.now()
        decided = record.set_retired(True, ctx, if_match=if_match, at=now)
        if decided is None:
            return record
        moved, change = decided
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=[credential_lock(record.credential_id)])
            referrers = self._credential_references.referrers_of([record.credential_id], transaction=locked)
            require_credential_unused(moved.slug, referrers[record.credential_id])
            return self._credentials.record_lifecycle(
                moved,
                retired=True,
                from_revision=record.revision,
                at=now,
                by=ctx.actor,
                change=change,
                transaction=locked,
            )

    def enable_credential(
        self, record: ConfiguredCredential, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredCredential:
        """Enable the credential, refused unless its forge is enabled and its secret active."""
        now = self._clock.now()
        decided = record.set_retired(False, ctx, if_match=if_match, at=now)
        if decided is None:
            return record
        moved, change = decided
        with self._credential_unit(moved, change.op, held=[credential_lock(record.credential_id)]) as (locked, _):
            return self._credentials.record_lifecycle(
                moved,
                retired=False,
                from_revision=record.revision,
                at=now,
                by=ctx.actor,
                change=change,
                transaction=locked,
            )

    @contextmanager
    def _credential_unit(
        self, record: ConfiguredCredential, op: ChangeOp, *, held: list[LockKey]
    ) -> Generator[tuple[LockedTransaction, NamedFacts]]:
        """The unit the ``op`` write of ``record`` runs in: it declares ``held`` and the keys of the forge and secret
        ``record`` names, then judges, under them, whether it may name them — yielding the facts it read."""
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=sorted([*held, *record.locks()]))
            binding = binding_named(record.fields.binding)
            facts = self._named_facts(record.locks(), locked)
            require_credential_references(record, op, binding, facts.forges, facts.secrets)
            yield locked, facts

    def credential_for_old_field(
        self, old: _OldField, ctx: ChangeContext, locked: LockedTransaction
    ) -> ConfiguredCredential:
        """The ``github.token`` credential the old field ``old`` resolves to on its forge, read in ``locked`` — the
        referrer's unit, which holds the forge's, the secret's and ``old.planned``'s keys — and created under
        ``old.planned`` when there is none, its slug by
        :func:`~blizzard.hub.domain.config.credential_resolution.new_credential_slug`.

        :class:`CredentialUnavailable` when it is retired, so the operator enables it rather than a write creating
        another; :class:`_Reresolve` when it is a credential other than ``old.planned``, the create's own lost race
        included — its insert runs in a savepoint, so the unit reads on."""
        authority = old_field_authority(old.secret_name)
        found = self._credentials.get_by_authority(old.forge_id, authority, transaction=locked)
        held = resolve_old_field(
            old.forge_id,
            old.secret_name,
            () if found is None else (found,),
            lambda: self._credentials.held_slugs(transaction=locked),
        )
        if isinstance(held, CredentialDraft):
            binding = binding_named(OLD_FIELD_BINDING)
            record, change = ConfiguredCredential.new(
                held, binding, ctx, at=self._clock.now(), credential_id=old.planned
            )
            facts = self._named_facts(record.locks(), locked)
            try:
                require_credential_references(record, change.op, binding, facts.forges, facts.secrets)
            except CredentialSecretUnavailable as refused:
                raise old.secret_refusal(refused) from None
            try:
                return self._credentials.create(record, change=change, transaction=locked)
            except (CredentialAuthorityTaken, CredentialSlugTaken):
                first = self._credentials.get_by_authority(old.forge_id, authority, transaction=locked)
                raise _Reresolve(old.planned if first is None else first.credential_id) from None
        if held.credential_id != old.planned:
            raise _Reresolve(held.credential_id)
        if held.retired:
            raise CredentialUnavailable(held.slug, retired=True)
        return held

    def _planned_credential(self, forge_id: ForgeId, secret_name: str) -> CredentialId:
        """The credential an old field naming ``secret_name`` on ``forge_id`` is expected to resolve to, read before
        its unit: the one acting as that authority, else a fresh id for the one it will create."""
        held = self._credentials.get_by_authority(forge_id, old_field_authority(secret_name))
        if held is not None:
            return held.credential_id
        return CredentialId(Id.mint_at(IdPrefix.CREDENTIAL, self._clock.now()).value)

    def _resolving[R](
        self, decision: CredentialDecision, field: str, forge_id: ForgeId, write: Callable[[_OldField | None], R]
    ) -> R:
        """Run ``write``, handing it the old field ``decision`` resolves on ``forge_id``, if any; run it once more
        locking the credential another write created first when the first run's unit read one."""
        if decision.resolve is None:
            return write(None)
        planned = self._planned_credential(forge_id, decision.resolve)
        try:
            return write(_OldField(field, forge_id, decision.resolve, planned))
        except _Reresolve as moved:
            return write(_OldField(field, forge_id, decision.resolve, moved.credential_id))

    # --- Work sources ------------------------------------------------------------

    def create_work_source_naming(
        self,
        slug: str,
        name: str | None,
        draft: WorkSourceDraft,
        choice: ForgeChoice,
        ctx: ChangeContext,
        *,
        credential: CredentialChoice = _NO_CREDENTIAL,
    ) -> Authored[ConfiguredWorkSource]:
        """Create a work source on the forge ``choice`` names, acting with the credential ``credential`` and the old
        ``secret`` field decide. The refusals of
        :func:`~blizzard.hub.domain.config.forge_resolution.resolve_forge` and
        :func:`~blizzard.hub.domain.config.credential_resolution.decide_credential`, and the record's own."""
        require_known_provider(draft.provider)
        forge = self._resolve(choice, provider=draft.provider, current=None)
        assert forge is not None
        decision = decide_credential(
            credential,
            UNSET if draft.secret is None else draft.secret,
            current=UNSET,
            field="secret",
            required=False,
        )

        def write(old: _OldField | None) -> Authored[ConfiguredWorkSource]:
            named = _named_credential(decision, old)
            fields = (
                draft.on(forge.forge_id)
                if named is None
                else draft.on(forge.forge_id, credential_id=named[0], secret=named[1])
            )
            record, change = ConfiguredWorkSource.new(slug, name, fields, ctx, at=self._clock.now())
            with self._work_source_unit(record, change.op, ctx, old=old) as (locked, resolved):
                written = self._work_sources.create(record, change=change, transaction=locked)
            return Authored(written, _used(old, resolved))

        return self._resolving(decision, "secret", forge.forge_id, write)

    def edit_work_source_naming(
        self,
        record: ConfiguredWorkSource,
        edit: WorkSourceEdit,
        choice: ForgeChoice,
        ctx: ChangeContext,
        *,
        if_match: int | None = None,
        credential: CredentialChoice = _NO_CREDENTIAL,
    ) -> Authored[ConfiguredWorkSource]:
        """Edit a work source, re-pointing it at the forge ``choice`` names, if any, and at the credential
        ``credential`` and the old ``secret`` field decide, as :meth:`create_work_source_naming` names them."""
        record.require_revision(if_match)
        provider = record.fields.provider if isinstance(edit.provider, UnsetType) else edit.provider
        require_known_provider(provider)
        forge = self._resolve(choice, provider=provider, current=record.fields.forge_id)
        moved = edit if forge is None else replace(edit, forge_id=forge.forge_id)
        forge_id = record.fields.forge_id if forge is None else forge.forge_id
        decision = decide_credential(
            credential,
            edit.secret,
            current=record.fields.secret,
            field="secret",
            required=False,
            forge_moved=forge_id != record.fields.forge_id,
        )

        def write(old: _OldField | None) -> Authored[ConfiguredWorkSource]:
            named = _named_credential(decision, old)
            sparse = moved if named is None else replace(moved, credential_id=named[0], secret=named[1])
            written, resolved = self._edit_work_source(record, sparse, ctx, if_match=if_match, old=old)
            return Authored(written, _used(old, resolved))

        return self._resolving(decision, "secret", forge_id, write)

    def create_work_source(
        self, slug: str, name: str | None, fields: WorkSourceFields, ctx: ChangeContext
    ) -> ConfiguredWorkSource:
        record, change = ConfiguredWorkSource.new(slug, name, fields, ctx, at=self._clock.now())
        with self._work_source_unit(record, change.op, ctx) as (locked, _):
            return self._work_sources.create(record, change=change, transaction=locked)

    def edit_work_source(
        self, record: ConfiguredWorkSource, edit: WorkSourceEdit, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredWorkSource:
        return self._edit_work_source(record, edit, ctx, if_match=if_match)[0]

    def _edit_work_source(
        self,
        record: ConfiguredWorkSource,
        edit: WorkSourceEdit,
        ctx: ChangeContext,
        *,
        if_match: int | None,
        old: _OldField | None = None,
    ) -> tuple[ConfiguredWorkSource, ConfiguredCredential | None]:
        decided = record.edit(edit, ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return record, None
        edited, change = decided
        with self._work_source_unit(edited, change.op, ctx, old=old) as (locked, resolved):
            written = self._work_sources.update(
                edited,
                from_revision=record.revision,
                left_slug=record.slug_left_for(edited),
                change=change,
                transaction=locked,
            )
        return written, resolved

    def retire_work_source(
        self, record: ConfiguredWorkSource, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredWorkSource:
        return self._set_work_source_retired(record, True, ctx, if_match)

    def enable_work_source(
        self, record: ConfiguredWorkSource, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredWorkSource:
        return self._set_work_source_retired(record, False, ctx, if_match)

    def _set_work_source_retired(
        self, record: ConfiguredWorkSource, retired: bool, ctx: ChangeContext, if_match: int | None
    ) -> ConfiguredWorkSource:
        now = self._clock.now()
        decided = record.set_retired(retired, ctx, if_match=if_match, at=now)
        if decided is None:
            return record
        moved, change = decided
        with self._work_source_unit(moved, change.op, ctx) as (locked, _):
            return self._work_sources.record_lifecycle(
                moved,
                retired=retired,
                from_revision=record.revision,
                at=now,
                by=ctx.actor,
                change=change,
                transaction=locked,
            )

    # --- Repositories ------------------------------------------------------------

    def create_repository_naming(
        self,
        slug: str,
        name: str | None,
        draft: RepositoryDraft,
        choice: ForgeChoice,
        ctx: ChangeContext,
        *,
        credential: CredentialChoice = _NO_CREDENTIAL,
    ) -> Authored[ConfiguredRepository]:
        """Create a repository on the forge ``choice`` names, acting with the credential ``credential`` and the old
        ``secret_name`` field decide, as :meth:`create_work_source_naming` names them."""
        forge = self._resolve(choice, provider=None, current=None)
        assert forge is not None
        decision = decide_credential(
            credential,
            UNSET if draft.secret_name is None else draft.secret_name,
            current=UNSET,
            field="secret_name",
            required=True,
        )

        def write(old: _OldField | None) -> Authored[ConfiguredRepository]:
            named = _named_credential(decision, old)
            fields = (
                draft.on(forge.forge_id)
                if named is None
                else draft.on(forge.forge_id, credential_id=named[0], secret_name=named[1])
            )
            record, change = ConfiguredRepository.new(slug, name, fields, ctx, at=self._clock.now())
            with self._repository_unit(record, change.op, ctx, offers=_CREATED, old=old) as (locked, resolved):
                written = self._repositories.create(record, change=change, transaction=locked)
            return Authored(written, _used(old, resolved))

        return self._resolving(decision, "secret_name", forge.forge_id, write)

    def edit_repository_naming(
        self,
        record: ConfiguredRepository,
        edit: RepositoryEdit,
        choice: ForgeChoice,
        ctx: ChangeContext,
        *,
        if_match: int | None = None,
        credential: CredentialChoice = _NO_CREDENTIAL,
    ) -> Authored[ConfiguredRepository]:
        """Edit a repository, re-pointing it at the forge ``choice`` names, if any, and at the credential
        ``credential`` and the old ``secret_name`` field decide, as :meth:`create_work_source_naming` names them."""
        record.require_revision(if_match)
        forge = self._resolve(choice, provider=None, current=record.fields.forge_id)
        moved = edit if forge is None else replace(edit, forge_id=forge.forge_id)
        forge_id = record.fields.forge_id if forge is None else forge.forge_id
        decision = decide_credential(
            credential,
            edit.secret_name,
            current=record.fields.secret_name,
            field="secret_name",
            required=False,
            forge_moved=forge_id != record.fields.forge_id,
        )

        def write(old: _OldField | None) -> Authored[ConfiguredRepository]:
            named = _named_credential(decision, old)
            sparse = moved if named is None else replace(moved, credential_id=named[0], secret_name=named[1])
            written, resolved = self._edit_repository(record, sparse, ctx, if_match=if_match, old=old)
            return Authored(written, _used(old, resolved))

        return self._resolving(decision, "secret_name", forge_id, write)

    def create_repository(
        self, slug: str, name: str | None, fields: RepositoryFields, ctx: ChangeContext
    ) -> ConfiguredRepository:
        record, change = ConfiguredRepository.new(slug, name, fields, ctx, at=self._clock.now())
        with self._repository_unit(record, change.op, ctx, offers=_CREATED) as (locked, _):
            return self._repositories.create(record, change=change, transaction=locked)

    def edit_repository(
        self, record: ConfiguredRepository, edit: RepositoryEdit, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredRepository:
        return self._edit_repository(record, edit, ctx, if_match=if_match)[0]

    def _edit_repository(
        self,
        record: ConfiguredRepository,
        edit: RepositoryEdit,
        ctx: ChangeContext,
        *,
        if_match: int | None,
        old: _OldField | None = None,
    ) -> tuple[ConfiguredRepository, ConfiguredCredential | None]:
        decided = record.edit(edit, ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return record, None
        edited, change = decided
        offers = (
            _EDITED
            if not isinstance(edit.landing_policy, UnsetType)
            or edited.fields.forge_id != record.fields.forge_id
            or edited.fields.credential_id != record.fields.credential_id
            else _UNCHECKED
        )
        with self._repository_unit(edited, change.op, ctx, offers=offers, old=old) as (locked, resolved):
            written = self._repositories.update(
                edited,
                from_revision=record.revision,
                left_slug=record.slug_left_for(edited),
                change=change,
                transaction=locked,
            )
        return written, resolved

    def retire_repository(
        self, record: ConfiguredRepository, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredRepository:
        return self._set_repository_retired(record, True, ctx, if_match)

    def enable_repository(
        self, record: ConfiguredRepository, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredRepository:
        return self._set_repository_retired(record, False, ctx, if_match)

    def _set_repository_retired(
        self, record: ConfiguredRepository, retired: bool, ctx: ChangeContext, if_match: int | None
    ) -> ConfiguredRepository:
        now = self._clock.now()
        decided = record.set_retired(retired, ctx, if_match=if_match, at=now)
        if decided is None:
            return record
        moved, change = decided
        with self._repository_unit(moved, change.op, ctx) as (locked, _):
            return self._repositories.record_lifecycle(
                moved,
                retired=retired,
                from_revision=record.revision,
                at=now,
                by=ctx.actor,
                change=change,
                transaction=locked,
            )

    # --- The unit a referrer write runs in -------------------------------------

    @contextmanager
    def _work_source_unit(
        self, record: ConfiguredWorkSource, op: ChangeOp, ctx: ChangeContext, *, old: _OldField | None = None
    ) -> Generator[tuple[LockedTransaction, ConfiguredCredential | None]]:
        """The unit the ``op`` write of ``record`` runs in: it declares the keys of the forge, secret and credential
        ``record`` names, resolves the old field ``old`` to its credential when one is given
        (:meth:`credential_for_old_field`), then judges, under them, whether it may name them — yielding the
        credential ``old`` resolved to."""
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=sorted(record.locks()))
            resolved = None if old is None else self.credential_for_old_field(old, ctx, locked)
            if op is not ChangeOp.RETIRE:
                require_work_source_references(record, op, self._named_facts(record.locks(), locked))
            yield locked, resolved

    @contextmanager
    def _repository_unit(
        self,
        record: ConfiguredRepository,
        op: ChangeOp,
        ctx: ChangeContext,
        *,
        offers: _OffersCheck = None,
        old: _OldField | None = None,
    ) -> Generator[tuple[LockedTransaction, ConfiguredCredential | None]]:
        """The unit the ``op`` write of ``record`` runs in, as :meth:`_work_source_unit` opens one, judging too —
        unless ``offers`` is ``None`` — whether its forge, through its credential, offers what its policy needs
        (:meth:`_require_offered`)."""
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=sorted(record.locks()))
            resolved = None if old is None else self.credential_for_old_field(old, ctx, locked)
            if op is not ChangeOp.RETIRE:
                facts = self._named_facts(record.locks(), locked)
                require_repository_references(record, op, facts)
                if offers is not None:
                    credential_id = record.fields.credential_id
                    credential = None if credential_id is None else facts.credentials.get(credential_id)
                    forge = facts.forges.get(record.fields.forge_id)
                    self._require_offered(record, forge, created=offers, credential=credential)
            yield locked, resolved

    def _named_facts(self, keys: Iterable[LockKey], locked: LockedTransaction) -> NamedFacts:
        """The forges, secrets and credentials ``keys`` lock, each read in ``locked``."""
        held = set(keys)
        forge_ids = sorted(ForgeId(key.key) for key in held if key.namespace is LockNamespace.FORGE)
        names = sorted(key.key for key in held if key.namespace is LockNamespace.SECRET)
        credential_ids = sorted(CredentialId(key.key) for key in held if key.namespace is LockNamespace.CREDENTIAL)
        return NamedFacts(
            forges=dict(self._forges.get_many(forge_ids, transaction=locked)) if forge_ids else {},
            secrets=dict(self._secrets.get_many(names, transaction=locked)) if names else {},
            credentials=(
                dict(self._credentials.get_many(credential_ids, transaction=locked)) if credential_ids else {}
            ),
        )

    # --- Apply -------------------------------------------------------------------

    def apply(
        self, declaration: ConfigDeclaration, stored: StoredConfig, ctx: ChangeContext, *, dry_run: bool
    ) -> tuple[EntryOutcome, ...]:
        """Reconcile ``declaration`` against ``stored`` and commit every write in one unit declaring the plan's locks,
        or — under ``dry_run`` — run that same unit and roll it back. The outcomes are the same either way.

        Each old secret field the declaration resolves (:attr:`ConfigDeclaration.old_fields`) resolves in that unit,
        after the forge writes and before every other, to its ``github.token`` credential, created when there is none
        (:meth:`credential_for_old_field`); the apply runs once more naming the credential another write created first
        when the unit read one. The credential writes land next, before any referrer's: a settings edit is held to what
        the enabled repositories acting with the credential need, and a secret move is mirrored to each record acting
        with it that the document leaves alone (:meth:`_settle_credentials`)."""
        try:
            return self._apply(declaration, stored, ctx, dry_run=dry_run)
        except _ApplyReresolve as moved:
            return self._apply(_naming(declaration, moved.planned, moved.credential_id), stored, ctx, dry_run=dry_run)

    def _apply(
        self, declaration: ConfigDeclaration, stored: StoredConfig, ctx: ChangeContext, *, dry_run: bool
    ) -> tuple[EntryOutcome, ...]:
        plan = reconcile(declaration, stored, ctx, at=self._clock.now())
        self._require_planned_offered(plan, stored.acting_credentials)
        if plan.writes or plan.positions:
            try:
                with self._transactions.begin() as transaction:
                    locked = transaction.lock_set(keys=plan.locks)
                    writes = plan.writes
                    if declaration.old_fields or declaration.credentials:
                        forges = tuple(write for write in writes if isinstance(write.record, ConfiguredForge))
                        if forges:
                            self._apply_writer.apply(forges, transaction=locked)
                        for old in declaration.old_fields:
                            self._resolve_declared(old, ctx, locked)
                        credentials = tuple(w for w in writes if isinstance(w.record, ConfiguredCredential))
                        if credentials:
                            self._apply_writer.apply(self._judged(credentials, locked), transaction=locked)
                            self._settle_credentials(credentials, declaration, ctx, locked)
                        writes = tuple(
                            w for w in writes if not isinstance(w.record, ConfiguredForge | ConfiguredCredential)
                        )
                    self._apply_writer.apply(
                        self._judged(writes, locked),
                        transaction=locked,
                        positions=plan.positions,
                        guards=plan.guards,
                    )
                    if dry_run:
                        raise _Rollback
            except _Rollback:
                pass
        return plan.outcomes

    def _settle_credentials(
        self,
        writes: Sequence[PlannedWrite],
        declaration: ConfigDeclaration,
        ctx: ChangeContext,
        locked: LockedTransaction,
    ) -> None:
        """For each credential edit of ``writes``, written in ``locked``: refuse a settings edit falling short of an
        enabled repository acting with the credential
        (:func:`~blizzard.hub.domain.config.credential_resolution.require_referrers_honoured`), and mirror a secret move
        to every record acting with it — each but the records the document declares, which carry the secret
        themselves."""
        declared = {
            *(entry.work_source_id for entry in declaration.work_sources),
            *(entry.repository_id for entry in declaration.repositories),
        }
        for write in writes:
            edited = write.record
            assert isinstance(edited, ConfiguredCredential)
            moved = {d.field for d in write.change.diff}
            if write.change.op is not ChangeOp.EDIT:
                continue
            try:
                if "settings" in moved:
                    forge = self._forges.get_many([edited.fields.forge_id], transaction=locked)[edited.fields.forge_id]
                    repositories = [
                        repo
                        for repo in self._repositories.naming_credential(edited.credential_id, transaction=locked)
                        if repo.repository_id not in declared
                    ]
                    require_referrers_honoured(
                        edited, [(repo, self._offers.offered(forge, repo.repository_id)) for repo in repositories]
                    )
            except ConfigFieldError as refused:
                raise ApplyEntryRefused(write.section, write.index, refused) from refused
            if "secret" in moved:
                self._mirror_secret(edited, ctx, locked, at=write.change.recorded_at, skip=declared)

    def _resolve_declared(self, old: DeclaredOldField, ctx: ChangeContext, locked: LockedTransaction) -> None:
        """Resolve the declared old field ``old`` in ``locked`` (:meth:`credential_for_old_field`), each refusal at its
        entry's old field."""
        try:
            self.credential_for_old_field(_OldField(old.field, old.forge_id, old.secret_name, old.planned), ctx, locked)
        except _Reresolve as moved:
            raise _ApplyReresolve(old.planned, moved.credential_id) from None
        except CredentialUnavailable as refused:
            unavailable = CredentialUnavailable(refused.credential, retired=refused.retired, field=old.field)
            raise ApplyEntryRefused(old.section, old.index, unavailable) from refused
        except ConfigFieldError as refused:
            raise ApplyEntryRefused(old.section, old.index, refused) from refused

    # --- Carry-over --------------------------------------------------------------

    def import_legacy(
        self,
        legacy: LegacyKeys,
        values: Mapping[str, str],
        commits: Sequence[CommitCoordinate],
        existing: ExistingConfig,
        *,
        recorded: bool,
        links: IImportLinks,
    ) -> ImportResult:
        """Carry ``legacy`` into records once, sealing each secret's value before the one write, and link each
        work source and repository it creates through ``links``. ``values`` holds the legacy variables' values;
        nothing is written when an import was already recorded or no legacy key is present."""
        if recorded:
            return ImportResult(ImportStatus.ALREADY_IMPORTED)
        if not legacy.present():
            return ImportResult(ImportStatus.NOTHING_TO_IMPORT)
        at = self._clock.now()
        plan = plan_import(legacy, values, commits, existing, ctx=MIGRATION_CONTEXT, at=at)
        forges = self._forges_by_id((*existing.forges, *(forge for forge, _ in plan.forges)))
        for repository, _ in plan.repositories:
            self._require_offered(repository, forges.get(repository.fields.forge_id), created=True)
        created = (
            [source.work_source_id for source, _ in plan.work_sources],
            [repo.repository_id for repo, _ in plan.repositories],
        )
        linked = links.links(*created, MIGRATION_CONTEXT, at=at) if any(created) else ()
        sealed = tuple(
            SealedSecretWrite(
                name=secret.name.value,
                sealed=self._cipher.seal(
                    SecretValue.entered(values[secret.variable]), name=secret.name.value, revision=1
                ),
                change=secret.change,
            )
            for secret in plan.secrets
        )
        imported = LegacyImport(
            secrets=sealed,
            forges=plan.forges,
            work_sources=plan.work_sources,
            repositories=plan.repositories,
            fact=plan.fact,
            links=linked,
        )
        try:
            with self._transactions.begin() as transaction:
                locked = transaction.lock_set(keys=imported.locks())
                if not self._import_writer.write(self._judged_import(imported, locked), transaction=locked):
                    raise _Rollback
        except _Rollback:
            return ImportResult(ImportStatus.ALREADY_IMPORTED)
        return ImportResult(ImportStatus.IMPORTED, plan.outcomes)

    def _judged(self, writes: Sequence[PlannedWrite], locked: LockedTransaction) -> tuple[PlannedWrite, ...]:
        """``writes``, each carrying the refusal its write earns for the forge, secret and credential it names, read
        under the apply's keys with the apply's own forge writes — every one ahead of any referrer — landed over
        them."""
        read = self._named_facts(
            (key for write in writes if isinstance(write.record, _NAMERS) for key in write.record.locks()), locked
        )
        planned = {write.record.forge_id: write.record for write in writes if isinstance(write.record, ConfiguredForge)}
        facts = replace(read, forges={**read.forges, **planned})
        return tuple(replace(write, verdict=_refusal(write, facts)) for write in writes)

    def _judged_import(self, imported: LegacyImport, locked: LockedTransaction) -> LegacyImport:
        """``imported``, carrying the refusal each work source and repository it creates earns for the forge and
        secret it names, read under the import's keys with the import's own secrets and forges landed over them."""
        records = [*(source for source, _ in imported.work_sources), *(repo for repo, _ in imported.repositories)]
        read = self._named_facts((key for record in records for key in record.locks()), locked)
        forges = {**read.forges, **{forge.forge_id: forge for forge, _ in imported.forges}}
        secrets = dict(read.secrets)
        for secret in imported.secrets:
            at, by = secret.change.recorded_at, secret.change.actor
            secrets[secret.name] = SecretMetadata(secret.name, 1, at, by, at, retired=False)
        facts = replace(read, forges=forges, secrets=secrets)
        verdicts = {
            _record_id(record): refused
            for record in records
            if (refused := references_refusal(record, ChangeOp.CREATE, facts)) is not None
        }
        return replace(imported, verdicts=verdicts)

    # --- Capabilities ------------------------------------------------------------

    def _require_offered(
        self,
        record: ConfiguredRepository,
        forge: ConfiguredForge | None,
        *,
        created: bool,
        credential: ConfiguredCredential | None = None,
    ) -> None:
        """:class:`ForgeCapabilityMissing` when ``forge`` does not offer what ``record``'s policy requires — asked for
        the repository, or for the forge's kind when the write ``created`` it, and narrowed by the declared offers of
        ``credential``, the one the record acts with, when it names one
        (:func:`~blizzard.hub.domain.config.credential_resolution.credential_offers`). A forge that does not exist is
        the record write's own refusal, not this check's."""
        if forge is None:
            return
        offered = self._offers.offered(forge, None if created else record.repository_id)
        if credential is not None:
            offered = credential_offers(credential, offered)
        require_offered(
            record.fields.landing_policy,
            offered,
            forge=forge.slug,
            credential=None if credential is None else credential.slug,
        )

    def _require_planned_offered(
        self, plan: ApplyPlan, credentials: Mapping[CredentialId, ConfiguredCredential]
    ) -> None:
        """Check every repository write ``plan`` holds, narrowed by the credential it acts with — one of
        ``credentials``, the held ones, or one ``plan`` writes — each refused at its own document entry."""
        planned = [write.record for write in plan.writes if isinstance(write.record, ConfiguredForge)]
        forges = self._forges_by_id((*self._forges.list_all(include_retired=True), *planned))
        acting = {
            **credentials,
            **{w.record.credential_id: w.record for w in plan.writes if isinstance(w.record, ConfiguredCredential)},
        }
        for write in plan.writes:
            if not isinstance(write.record, ConfiguredRepository):
                continue
            try:
                forge = forges.get(write.record.fields.forge_id)
                credential_id = write.record.fields.credential_id
                credential = None if credential_id is None else acting.get(credential_id)
                self._require_offered(write.record, forge, created=write.from_revision is None, credential=credential)
            except ConfigFieldError as exc:
                raise ApplyEntryRefused(write.section, write.index, exc) from exc

    @staticmethod
    def _forges_by_id(forges: Sequence[ConfiguredForge]) -> dict[ForgeId, ConfiguredForge]:
        return {forge.forge_id: forge for forge in forges}

    # --- Secrets -----------------------------------------------------------------

    def create_secret(self, name: SecretName, value: str, ctx: ChangeContext) -> SecretMetadata:
        sealed = self._cipher.seal(SecretValue.entered(value), name=name.value, revision=1)
        now = self._clock.now()
        change = secret_lifecycle.creation(name, ctx, at=now)
        return self._secrets.create(name.value, sealed=sealed, at=now, by=ctx.actor, change=change)

    def replace_secret(
        self, record: SecretMetadata, value: str, ctx: ChangeContext, *, if_match: int | None = None
    ) -> SecretMetadata:
        """Seal under the next revision and compare-and-set from ``record.revision``.
        ``if_match`` is the revision the caller last saw, checked before any write."""
        now = self._clock.now()
        change = secret_lifecycle.replacement(record, ctx, retired=record.retired, if_match=if_match, at=now)
        sealed = self._cipher.seal(SecretValue.entered(value), name=record.name, revision=change.revision)
        return self._secrets.replace(
            record.name, from_revision=record.revision, sealed=sealed, at=now, by=ctx.actor, change=change
        )

    def retire_secret(self, record: SecretMetadata, ctx: ChangeContext) -> bool:
        """Retire the secret; ``False`` when it already was. :class:`SecretReferenced` when an
        active record names it."""
        return self._set_secret_retired(record, True, ctx)

    def enable_secret(self, record: SecretMetadata, ctx: ChangeContext) -> bool:
        """Re-enable the secret; ``False`` when it already was active."""
        return self._set_secret_retired(record, False, ctx)

    def _set_secret_retired(self, record: SecretMetadata, retired: bool, ctx: ChangeContext) -> bool:
        now = self._clock.now()
        change = secret_lifecycle.lifecycle_change(record, ctx, retired_now=record.retired, retired=retired, at=now)
        if change is None:
            return False
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=[secret_lock(record.name)])
            if retired:
                referrers = self._secret_references.referrers_of([record.name], transaction=locked)
                require_unreferenced(record.name, referrers[record.name])
            self._secrets.record_lifecycle(
                record.name, retired=retired, at=now, by=ctx.actor, change=change, transaction=locked
            )
        return True


def _naming(declaration: ConfigDeclaration, planned: CredentialId, moved: CredentialId) -> ConfigDeclaration:
    """``declaration`` with every write and old field naming the credential ``planned`` naming ``moved`` instead."""

    def named(credential_id: CredentialId | None | UnsetType) -> CredentialId | None | UnsetType:
        return moved if credential_id == planned else credential_id

    return replace(
        declaration,
        work_sources=tuple(
            replace(
                entry,
                fields=replace(entry.fields, credential_id=named(entry.fields.credential_id)),
                edit=replace(entry.edit, credential_id=named(entry.edit.credential_id)),
            )
            for entry in declaration.work_sources
        ),
        repositories=tuple(
            replace(
                entry,
                fields=replace(entry.fields, credential_id=named(entry.fields.credential_id)),
                edit=replace(entry.edit, credential_id=named(entry.edit.credential_id)),
            )
            for entry in declaration.repositories
        ),
        old_fields=tuple(
            replace(old, planned=moved) if old.planned == planned else old for old in declaration.old_fields
        ),
    )


#: The records whose writes name a forge and a secret.
_REFERRERS = (ConfiguredWorkSource, ConfiguredRepository)

#: The records whose writes name a forge and a secret, a credential among them.
_NAMERS = (*_REFERRERS, ConfiguredCredential)


def _refusal(write: PlannedWrite, facts: NamedFacts) -> ConfigFieldError | None:
    """The refusal ``write`` earns for what its record names, judged on ``facts``, or ``None``."""
    record = write.record
    if not isinstance(record, ConfiguredCredential):
        return references_refusal(record, write.change.op, facts)
    try:
        binding = binding_named(record.fields.binding)
        require_credential_references(record, write.change.op, binding, facts.forges, facts.secrets)
    except ConfigFieldError as refused:
        return refused
    return None


def _record_id(record: ConfiguredWorkSource | ConfiguredRepository) -> str:
    return record.work_source_id if isinstance(record, ConfiguredWorkSource) else record.repository_id
