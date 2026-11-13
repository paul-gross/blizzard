"""``ConfigAuthoring`` — the one writer of configured records.

Every verb takes the record the edge resolved (``bzh:domain-takes-objects``), asks the record's model for the change,
and hands it to the write repository with the record write. A write that changes nothing writes nothing. Secrets
and forges route through here too.

A repository or work-source write from a door goes through the ``…_naming`` verbs, which take the forge the write names
as the edge resolved it: the forge is decided first
(:func:`~blizzard.hub.domain.config.forge_resolution.resolve_forge`), then the record is built and validated on it.

Every repository write that sets a landing policy — a create, a policy edit, a forge change, and each repository write
an apply or an import plans — is checked against what :class:`IForgeOffers` says its forge offers, and refused
:class:`~blizzard.hub.domain.config.landing_policies.ForgeCapabilityMissing` before anything is written."""

from __future__ import annotations

from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import replace

from blizzard.foundation.clock import IClock
from blizzard.foundation.secrets.sealing import ISecretCipher
from blizzard.foundation.secrets.value import SecretValue
from blizzard.hub.domain.config import secret_lifecycle
from blizzard.hub.domain.config.apply import (
    ApplyEntryRefused,
    ApplyPlan,
    ConfigDeclaration,
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
    require_known_provider,
)
from blizzard.hub.domain.kernel.locks import LockKey
from blizzard.hub.domain.kernel.transactions import IHubTransactions, LockedTransaction
from blizzard.hub.domain.kernel.unset import UnsetType


class _Rollback(Exception):
    """Raised out of a unit that must leave nothing behind — a dry run, or an import recorded first."""


class ConfigAuthoring:
    def __init__(
        self,
        *,
        work_sources: IWriteWorkSourceRepository,
        repositories: IWriteRepositoryRecordRepository,
        forges: IWriteForgeRepository,
        offers: IForgeOffers,
        secrets: IWriteSecretRepository,
        cipher: ISecretCipher,
        apply_writer: IConfigApplyWriter,
        import_writer: IConfigImportWriter,
        transactions: IHubTransactions,
        secret_references: ISecretReferences,
        forge_references: IForgeReferences,
        clock: IClock,
    ) -> None:
        """``offers`` answers what a repository's forge offers its landing policy; ``transactions`` opens the unit
        each write runs in, and ``secret_references`` and ``forge_references`` read, in it, the records naming a secret
        or a forge being retired."""
        self._work_sources = work_sources
        self._repositories = repositories
        self._forges = forges
        self._offers = offers
        self._secrets = secrets
        self._cipher = cipher
        self._apply_writer = apply_writer
        self._import_writer = import_writer
        self._transactions = transactions
        self._secret_references = secret_references
        self._forge_references = forge_references
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

    # --- Work sources ------------------------------------------------------------

    def create_work_source_naming(
        self, slug: str, name: str | None, draft: WorkSourceDraft, choice: ForgeChoice, ctx: ChangeContext
    ) -> ConfiguredWorkSource:
        """Create a work source on the forge ``choice`` names. The refusals of
        :func:`~blizzard.hub.domain.config.forge_resolution.resolve_forge`, and the record's own."""
        require_known_provider(draft.provider)
        forge = self._resolve(choice, provider=draft.provider, current=None)
        assert forge is not None
        return self.create_work_source(slug, name, draft.on(forge.forge_id), ctx)

    def edit_work_source_naming(
        self,
        record: ConfiguredWorkSource,
        edit: WorkSourceEdit,
        choice: ForgeChoice,
        ctx: ChangeContext,
        *,
        if_match: int | None = None,
    ) -> ConfiguredWorkSource:
        """Edit a work source, re-pointing it at the forge ``choice`` names, if any, as
        :meth:`create_work_source_naming` names one."""
        record.require_revision(if_match)
        provider = record.fields.provider if isinstance(edit.provider, UnsetType) else edit.provider
        require_known_provider(provider)
        forge = self._resolve(choice, provider=provider, current=record.fields.forge_id)
        moved = edit if forge is None else replace(edit, forge_id=forge.forge_id)
        return self.edit_work_source(record, moved, ctx, if_match=if_match)

    def create_work_source(
        self, slug: str, name: str | None, fields: WorkSourceFields, ctx: ChangeContext
    ) -> ConfiguredWorkSource:
        record, change = ConfiguredWorkSource.new(slug, name, fields, ctx, at=self._clock.now())
        with self._work_source_unit(record, change.op) as locked:
            return self._work_sources.create(record, change=change, transaction=locked)

    def edit_work_source(
        self, record: ConfiguredWorkSource, edit: WorkSourceEdit, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredWorkSource:
        decided = record.edit(edit, ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return record
        edited, change = decided
        with self._work_source_unit(edited, change.op) as locked:
            return self._work_sources.update(
                edited,
                from_revision=record.revision,
                left_slug=record.slug_left_for(edited),
                change=change,
                transaction=locked,
            )

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
        with self._work_source_unit(moved, change.op) as locked:
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
        self, slug: str, name: str | None, draft: RepositoryDraft, choice: ForgeChoice, ctx: ChangeContext
    ) -> ConfiguredRepository:
        """Create a repository on the forge ``choice`` names, as :meth:`create_work_source_naming` names one."""
        forge = self._resolve(choice, provider=None, current=None)
        assert forge is not None
        return self.create_repository(slug, name, draft.on(forge.forge_id), ctx)

    def edit_repository_naming(
        self,
        record: ConfiguredRepository,
        edit: RepositoryEdit,
        choice: ForgeChoice,
        ctx: ChangeContext,
        *,
        if_match: int | None = None,
    ) -> ConfiguredRepository:
        """Edit a repository, re-pointing it at the forge ``choice`` names, if any, as
        :meth:`create_work_source_naming` names one."""
        record.require_revision(if_match)
        forge = self._resolve(choice, provider=None, current=record.fields.forge_id)
        moved = edit if forge is None else replace(edit, forge_id=forge.forge_id)
        return self.edit_repository(record, moved, ctx, if_match=if_match)

    def create_repository(
        self, slug: str, name: str | None, fields: RepositoryFields, ctx: ChangeContext
    ) -> ConfiguredRepository:
        record, change = ConfiguredRepository.new(slug, name, fields, ctx, at=self._clock.now())
        self._require_offered(record, self._forges.get(record.fields.forge_id), created=True)
        with self._repository_unit(record, change.op) as locked:
            return self._repositories.create(record, change=change, transaction=locked)

    def edit_repository(
        self, record: ConfiguredRepository, edit: RepositoryEdit, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredRepository:
        decided = record.edit(edit, ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return record
        edited, change = decided
        if not isinstance(edit.landing_policy, UnsetType) or edited.fields.forge_id != record.fields.forge_id:
            self._require_offered(edited, self._forges.get(edited.fields.forge_id), created=False)
        with self._repository_unit(edited, change.op) as locked:
            return self._repositories.update(
                edited,
                from_revision=record.revision,
                left_slug=record.slug_left_for(edited),
                change=change,
                transaction=locked,
            )

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
        with self._repository_unit(moved, change.op) as locked:
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
    def _work_source_unit(self, record: ConfiguredWorkSource, op: ChangeOp) -> Generator[LockedTransaction]:
        """The unit the ``op`` write of ``record`` runs in: it declares the keys of the forge and secret ``record``
        names, then judges, under them, whether it may name them."""
        with self._referrer_unit(record.fields.forge_id, record.fields.secret) as (locked, forges, secrets):
            if op is not ChangeOp.RETIRE:
                require_work_source_references(record, op, forges(), secrets())
            yield locked

    @contextmanager
    def _repository_unit(self, record: ConfiguredRepository, op: ChangeOp) -> Generator[LockedTransaction]:
        """The unit the ``op`` write of ``record`` runs in, as :meth:`_work_source_unit` opens one."""
        with self._referrer_unit(record.fields.forge_id, record.fields.secret_name) as (locked, forges, secrets):
            if op is not ChangeOp.RETIRE:
                require_repository_references(record, op, forges(), secrets())
            yield locked

    @contextmanager
    def _referrer_unit(
        self, forge_id: ForgeId, secret: str | None
    ) -> Generator[
        tuple[
            LockedTransaction,
            Callable[[], Mapping[ForgeId, ConfiguredForge]],
            Callable[[], Mapping[str, SecretMetadata]],
        ]
    ]:
        """One unit declaring the keys of ``forge_id`` and ``secret``, and the reads of each that join it."""
        keys: set[LockKey] = {forge_lock(forge_id)}
        if secret is not None:
            keys.add(secret_lock(secret))
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=sorted(keys))
            yield (
                locked,
                lambda: self._forges.get_many([forge_id], transaction=locked),
                lambda: {} if secret is None else self._secrets.get_many([secret], transaction=locked),
            )

    # --- Apply -------------------------------------------------------------------

    def apply(
        self, declaration: ConfigDeclaration, stored: StoredConfig, ctx: ChangeContext, *, dry_run: bool
    ) -> tuple[EntryOutcome, ...]:
        """Reconcile ``declaration`` against ``stored`` and commit every write in one unit declaring the plan's locks,
        or — under ``dry_run`` — run that same unit and roll it back. The outcomes are the same either way."""
        plan = reconcile(declaration, stored, ctx, at=self._clock.now())
        self._require_planned_offered(plan)
        if plan.writes or plan.positions:
            try:
                with self._transactions.begin() as transaction:
                    locked = transaction.lock_set(keys=plan.locks)
                    self._apply_writer.apply(
                        self._judged(plan.writes, locked),
                        transaction=locked,
                        positions=plan.positions,
                        guards=plan.guards,
                    )
                    if dry_run:
                        raise _Rollback
            except _Rollback:
                pass
        return plan.outcomes

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
        """``writes``, each carrying the refusal its write earns for the forge and secret it names, read under the
        apply's keys with the apply's own forge writes — every one ahead of any referrer — landed over them."""
        forges = dict(self._forges.get_many(sorted(_named_forges(writes)), transaction=locked))
        forges.update(
            {write.record.forge_id: write.record for write in writes if isinstance(write.record, ConfiguredForge)}
        )
        secrets = self._secrets.get_many(sorted(_named_secrets(writes)), transaction=locked)
        return tuple(
            replace(write, verdict=references_refusal(write.record, write.change.op, forges, secrets))
            for write in writes
        )

    def _judged_import(self, imported: LegacyImport, locked: LockedTransaction) -> LegacyImport:
        """``imported``, carrying the refusal each work source and repository it creates earns for the forge and
        secret it names, read under the import's keys with the import's own secrets and forges landed over them."""
        records = [*(source for source, _ in imported.work_sources), *(repo for repo, _ in imported.repositories)]
        forges = dict(self._forges.get_many(sorted({record.fields.forge_id for record in records}), transaction=locked))
        forges.update({forge.forge_id: forge for forge, _ in imported.forges})
        named = {
            name
            for record in records
            if (name := record.fields.secret if isinstance(record, ConfiguredWorkSource) else record.fields.secret_name)
        }
        secrets = dict(self._secrets.get_many(sorted(named), transaction=locked))
        for secret in imported.secrets:
            at, by = secret.change.recorded_at, secret.change.actor
            secrets[secret.name] = SecretMetadata(secret.name, 1, at, by, at, retired=False)
        verdicts = {
            _record_id(record): refused
            for record in records
            if (refused := references_refusal(record, ChangeOp.CREATE, forges, secrets)) is not None
        }
        return replace(imported, verdicts=verdicts)

    # --- Capabilities ------------------------------------------------------------

    def _require_offered(self, record: ConfiguredRepository, forge: ConfiguredForge | None, *, created: bool) -> None:
        """:class:`ForgeCapabilityMissing` when ``forge`` does not offer what ``record``'s policy requires — asked for
        the repository, or for the forge's kind when the write ``created`` it. A forge that does not exist is the
        record write's own refusal, not this check's."""
        if forge is None:
            return
        offered = self._offers.offered(forge, None if created else record.repository_id)
        require_offered(record.fields.landing_policy, offered, forge=forge.slug)

    def _require_planned_offered(self, plan: ApplyPlan) -> None:
        """Check every repository write ``plan`` holds, each refused at its own document entry."""
        planned = [write.record for write in plan.writes if isinstance(write.record, ConfiguredForge)]
        forges = self._forges_by_id((*self._forges.list_all(include_retired=True), *planned))
        for write in plan.writes:
            if not isinstance(write.record, ConfiguredRepository):
                continue
            try:
                forge = forges.get(write.record.fields.forge_id)
                self._require_offered(write.record, forge, created=write.from_revision is None)
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


def _named_forges(writes: Sequence[PlannedWrite]) -> set[ForgeId]:
    return {
        write.record.fields.forge_id
        for write in writes
        if isinstance(write.record, ConfiguredWorkSource | ConfiguredRepository)
    }


def _named_secrets(writes: Sequence[PlannedWrite]) -> set[str]:
    named: set[str] = set()
    for write in writes:
        if isinstance(write.record, ConfiguredWorkSource) and write.record.fields.secret is not None:
            named.add(write.record.fields.secret)
        elif isinstance(write.record, ConfiguredRepository):
            named.add(write.record.fields.secret_name)
    return named


def _record_id(record: ConfiguredWorkSource | ConfiguredRepository) -> str:
    return record.work_source_id if isinstance(record, ConfiguredWorkSource) else record.repository_id
