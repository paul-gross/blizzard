import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { form } from '@angular/forms/signals';
import {
  asyncState,
  type EditBaseline,
  errorMessage,
  type KitAsyncStateValue,
  restingAsyncState,
  submitForm,
  ViewportService,
} from 'fleet';
import { map } from 'rxjs';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { ProjectLens } from '../../core/project-lens';
import { injectRepositoryLifecycleMutation } from './repositories.mutations';
import { type ConfigFailure, configActions, failureFor } from '../config-actions.model';
import { ConfigFieldTreeDialog } from '../config-field-tree-dialog';
import { detailCliCommand } from '../config-cli.model';
import { lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { ConfigRecordPanel } from '../config-record-panel';
import { CredentialCheck } from '../credentials/credential-check';
import { credentialCheckTarget } from '../credentials/credentials.model';
import { honourForge, repositoryFieldViews, repositoryRecordVm } from './repositories.model';
import { injectLandingPolicyChoicesQuery, injectRepositoryRecordQuery } from './repositories.query';
import { EMPTY_REPOSITORY_DRAFT, REPOSITORY_RECORD_KIND, type RepositoryDraft } from './repository.domain';
import { repositoryEditRules } from './repository.form';
import { injectRepositoryService } from './repository.service';
import { toEditBaseline } from './repository.translate';
import { injectCredentialChoicesQuery } from '../credentials/credentials.query';
import { injectForgeChoicesQuery, injectForgeHonourQuery } from '../forges/forges.query';

/**
 * The selected repository's detail — the right-hand child of `/admin/repositories`,
 * mounted by both its bare child (nothing selected) and `:key`. A container: it reads
 * the record and its change history and forwards plain view models to the
 * presentational {@link ConfigRecordPanel}.
 */
@Component({
  selector: 'app-repository-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFieldTreeDialog, ConfigRecordPanel, CredentialCheck],
  templateUrl: './repository-detail.html',
})
export class RepositoryDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly lens = inject(ProjectLens);
  private readonly viewport = inject(ViewportService);

  private readonly key = toSignal(this.route.paramMap.pipe(map((params) => params.get('key'))), {
    initialValue: null,
  });
  private readonly repositoryQuery = injectRepositoryRecordQuery(() => this.key());
  private readonly historyQuery = injectConfigHistoryQuery(REPOSITORY_RECORD_KIND, () =>
    this.repositoryQuery.data()?.id ?? null,
  );

  protected readonly vm = computed(() => repositoryRecordVm(this.repositoryQuery.data()));
  protected readonly state = computed<KitAsyncStateValue>(() =>
    restingAsyncState(this.key() === null, this.repositoryQuery, this.vm() === null),
  );
  protected readonly lastChange = computed(() => lastChange(this.historyQuery.data()));
  protected readonly revisions = computed(() => revisionRows(this.historyQuery.data()));
  protected readonly revisionsState = computed<KitAsyncStateValue>(() =>
    asyncState(this.historyQuery, this.revisions().length === 0),
  );
  protected readonly cliCommand = computed(() =>
    detailCliCommand(this.viewport.mode(), REPOSITORY_RECORD_KIND, this.repositoryQuery.data()?.slug),
  );

  private readonly meQuery = injectMeQuery();
  private readonly service = injectRepositoryService();
  private readonly lifecycleMutation = injectRepositoryLifecycleMutation();

  private readonly forgesQuery = injectForgeChoicesQuery(() => false);
  private readonly credentialsQuery = injectCredentialChoicesQuery(() => false);
  private readonly landingPoliciesQuery = injectLandingPolicyChoicesQuery();
  /** What the open edit started from, captured from one read; `null` while no edit is open. */
  private readonly baseline = signal<EditBaseline<RepositoryDraft> | null>(null);
  private readonly draft = signal<RepositoryDraft>({ ...EMPTY_REPOSITORY_DRAFT });
  protected readonly editForm = form(this.draft, repositoryEditRules);
  protected readonly editBaseline = computed(() => this.baseline()?.draft ?? null);
  /** The forge the edit form names — its detail says which policies it can honour. */
  private readonly honourQuery = injectForgeHonourQuery(() => honourForge(this.baseline() !== null, this.draft()));
  protected readonly fields = computed(() =>
    repositoryFieldViews(
      this.forgesQuery.data() ?? [],
      this.credentialsQuery.data() ?? [],
      this.landingPoliciesQuery.data() ?? null,
      this.honourQuery.data() ?? null,
    ),
  );
  private readonly pendingKey = signal<string | null>(null);
  private readonly failure = signal<ConfigFailure | null>(null);

  protected readonly actions = computed(() =>
    configActions(this.viewport.mode(), hasPermission(this.meQuery.data(), 'config:edit'), this.repositoryQuery.data()),
  );
  /** The Check action's credential and scope — this record alone, shown to an editor when it names a credential. */
  protected readonly check = computed(() =>
    credentialCheckTarget(hasPermission(this.meQuery.data(), 'config:edit'), this.repositoryQuery.data()?.credential?.id, {
      field: 'repositories',
      id: this.repositoryQuery.data()?.id,
    }),
  );
  protected readonly busy = computed(() => this.pendingKey() !== null && this.pendingKey() === this.key());
  protected readonly actionError = computed(() => failureFor(this.failure(), this.key()));

  constructor() {
    // A slug or former slug in the URL opens the repository; the URL then names it by id.
    effect(() => {
      const repository = this.repositoryQuery.data();
      const key = this.key();
      if (repository && key !== null && key !== repository.id) {
        void this.router.navigate(this.lens.link(['/admin', 'repositories', repository.id]), {
          replaceUrl: true,
          queryParamsHandling: 'preserve',
        });
      }
    });
  }

  /** Edit captures the baseline from the record as read now; a later refresh of the read replaces neither it nor the
   * working draft. */
  protected onEdit(): void {
    const record = this.repositoryQuery.data();
    if (!record) return;
    const baseline = toEditBaseline(record);
    this.editForm().reset({ ...baseline.draft });
    this.baseline.set(baseline);
  }

  /** Saves the working draft against the baseline; a refused save keeps both open. */
  protected async onSave(): Promise<void> {
    const baseline = this.baseline();
    if (baseline === null) return;
    const saved = await submitForm(this.editForm, (draft) => this.service.edit(baseline, draft));
    if (saved && this.baseline() === baseline) this.baseline.set(null);
  }

  protected onCloseEdit(): void {
    this.baseline.set(null);
  }

  protected onLifecycle(verb: 'retire' | 'enable'): void {
    const record = this.repositoryQuery.data();
    if (!record) return;
    this.pendingKey.set(record.id);
    this.failure.set(null);
    this.lifecycleMutation.mutate(
      {
        repositoryId: record.id,
        revision: record.revision,
        retired: verb === 'retire',
      },
      {
        onError: (error) =>
          this.failure.set({
            key: record.id,
            message: errorMessage(error, `${verb} failed.`),
          }),
        onSettled: () => this.pendingKey.set(null),
      },
    );
  }
}
