import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { form } from '@angular/forms/signals';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { asyncState, errorMessage, restingAsyncState } from 'fleet/core';
import { type EditBaseline, submitForm } from 'fleet/forms';
import { type KitAsyncStateValue } from 'fleet/kit';
import { ViewportService } from 'fleet/viewport';
import { map } from 'rxjs';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { ProjectLens } from '../../core/project-lens';
import { injectWorkSourceLifecycleMutation } from './work-sources.mutations';
import { type ConfigFailure, failureFor } from '../config-actions.model';
import { ConfigFieldTreeDialog } from '../config-field-tree-dialog';
import { detailCliCommand } from '../config-cli.model';
import { lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { ConfigRecordPanel } from '../config-record-panel';
import { CredentialCheck } from '../credentials/credential-check';
import { EMPTY_WORK_SOURCE_DRAFT, type WorkSourceDraft } from './work-source.domain';
import { workSourceEditRules } from './work-source.form';
import { injectWorkSourceService } from './work-source.service';
import { toEditBaseline, WORK_SOURCE_RECORD_KIND } from './work-source.translate';
import { workSourceActions, workSourceCliName, workSourceFieldViews, workSourceRecordVm } from './work-sources.model';
import { injectWorkSourceQuery } from './work-sources.query';
import { credentialCheckTarget, credentialOptions } from '../credentials/credentials.model';
import { injectCredentialChoicesQuery } from '../credentials/credentials.query';
import { forgeOptions } from '../forges/forges.model';
import { injectForgeListQuery } from '../forges/forges.query';

/**
 * The selected work source's detail — the right-hand child of `/admin/work-sources`,
 * mounted by both its bare child (nothing selected) and `:key`. A container: it reads
 * the source and its change history and forwards plain view models to the
 * presentational {@link ConfigRecordPanel}.
 */
@Component({
  selector: 'app-work-source-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFieldTreeDialog, ConfigRecordPanel, CredentialCheck],
  templateUrl: './work-source-detail.html',
})
export class WorkSourceDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly lens = inject(ProjectLens);
  private readonly viewport = inject(ViewportService);

  private readonly key = toSignal(this.route.paramMap.pipe(map((params) => params.get('key'))), {
    initialValue: null,
  });
  private readonly sourceQuery = injectWorkSourceQuery(() => this.key());
  private readonly historyQuery = injectConfigHistoryQuery(WORK_SOURCE_RECORD_KIND, () =>
    this.sourceQuery.data()?.id ?? null,
  );

  protected readonly vm = computed(() => workSourceRecordVm(this.sourceQuery.data()));
  protected readonly state = computed<KitAsyncStateValue>(() =>
    restingAsyncState(this.key() === null, this.sourceQuery, this.vm() === null),
  );
  protected readonly lastChange = computed(() => lastChange(this.historyQuery.data()));
  protected readonly revisions = computed(() => revisionRows(this.historyQuery.data()));
  protected readonly revisionsState = computed<KitAsyncStateValue>(() =>
    asyncState(this.historyQuery, this.revisions().length === 0),
  );
  protected readonly cliCommand = computed(() =>
    detailCliCommand(this.viewport.mode(), WORK_SOURCE_RECORD_KIND, workSourceCliName(this.sourceQuery.data())),
  );

  private readonly meQuery = injectMeQuery();
  private readonly workSourceService = injectWorkSourceService();
  private readonly lifecycleMutation = injectWorkSourceLifecycleMutation();

  private readonly forgesQuery = injectForgeListQuery(() => false);
  private readonly credentialsQuery = injectCredentialChoicesQuery(() => false);
  protected readonly fields = computed(() =>
    workSourceFieldViews(
      forgeOptions(this.forgesQuery.data() ?? []),
      credentialOptions(this.credentialsQuery.data() ?? []),
    ),
  );

  /** What the edit started from — `null` while no edit is under way. */
  protected readonly editBaseline = signal<EditBaseline<WorkSourceDraft> | null>(null);
  private readonly editDraft = signal<WorkSourceDraft>(EMPTY_WORK_SOURCE_DRAFT);
  protected readonly editForm = form(this.editDraft, workSourceEditRules);
  private readonly pendingKey = signal<string | null>(null);
  private readonly failure = signal<ConfigFailure | null>(null);

  protected readonly actions = computed(() =>
    workSourceActions(this.viewport.mode(), hasPermission(this.meQuery.data(), 'config:edit'), this.sourceQuery.data()),
  );
  /** The Check action's credential and scope — this record alone, shown to an editor when it names a credential. */
  protected readonly check = computed(() =>
    credentialCheckTarget(hasPermission(this.meQuery.data(), 'config:edit'), this.sourceQuery.data()?.credentialId, {
      field: 'work_sources',
      id: this.sourceQuery.data()?.id,
    }),
  );
  protected readonly busy = computed(() => this.pendingKey() !== null && this.pendingKey() === this.key());
  protected readonly actionError = computed(() => failureFor(this.failure(), this.key()));

  constructor() {
    // A slug or former slug in the URL opens the work source; the URL then names it by id.
    effect(() => {
      const source = this.sourceQuery.data();
      const key = this.key();
      const id = source?.id;
      if (id && key !== null && key !== id) {
        void this.router.navigate(this.lens.link(['/admin', 'work-sources', id]), {
          replaceUrl: true,
          queryParamsHandling: 'preserve',
        });
      }
    });
    // Selecting another work source ends the edit.
    effect(() => {
      this.key();
      untracked(() => this.editBaseline.set(null));
    });
  }

  /** Captures the baseline — id, revision, and draft from one read — and a working copy of its draft. */
  protected onEdit(): void {
    const source = this.sourceQuery.data();
    const baseline = source && toEditBaseline(source);
    if (!baseline) return;
    this.editForm().reset({ ...baseline.draft });
    this.editBaseline.set(baseline);
  }

  /** Sends the working draft against the baseline, so a refresh of the read changes neither. */
  protected async onSave(): Promise<void> {
    const baseline = this.editBaseline();
    if (baseline === null) return;
    const saved = await submitForm(this.editForm, (draft) => this.workSourceService.edit(baseline, draft));
    if (saved && this.editBaseline() === baseline) this.editBaseline.set(null);
  }

  protected onCancel(): void {
    this.editBaseline.set(null);
  }

  protected onLifecycle(verb: 'retire' | 'enable'): void {
    const record = this.sourceQuery.data();
    const id = record?.id;
    if (!record || !id || record.revision == null) return;
    this.pendingKey.set(id);
    this.failure.set(null);
    this.lifecycleMutation.mutate(
      {
        workSourceId: id,
        revision: record.revision,
        retired: verb === 'retire',
      },
      {
        onError: (error) =>
          this.failure.set({
            key: id,
            message: errorMessage(error, `${verb} failed.`),
          }),
        onSettled: () => this.pendingKey.set(null),
      },
    );
  }
}
