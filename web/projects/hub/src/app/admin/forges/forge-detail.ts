import { ChangeDetectionStrategy, Component, computed, effect, inject, signal, untracked } from '@angular/core';
import { form } from '@angular/forms/signals';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { asyncState, type EditBaseline, errorMessage, type KitAsyncStateValue, restingAsyncState, submitForm, ViewportService } from 'fleet';
import { map } from 'rxjs';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { ProjectLens } from '../../core/project-lens';
import { type ConfigFailure, configActions, failureFor } from '../config-actions.model';
import { detailCliCommand } from '../config-cli.model';
import { ConfigFieldTreeDialog } from '../config-field-tree-dialog';
import { lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { ConfigRecordPanel } from '../config-record-panel';
import { EMPTY_FORGE_DRAFT, type ForgeDraft } from '../../forge/forge.domain';
import { forgeEditRules } from '../../forge/forge.form';
import { injectForgeService } from '../../forge/forge.service';
import { toForgeEditBaseline } from '../../forge/forge.translate';
import { injectForgeLifecycleMutation } from '../../forge/forges.mutations';
import { FORGE_FIELD_VIEWS, forgeRecordVm } from './forges.model';
import { injectForgeRecordQuery } from './forges.query';

@Component({
  selector: 'app-forge-detail', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFieldTreeDialog, ConfigRecordPanel], templateUrl: './forge-detail.html',
})
export class ForgeDetailPage {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly lens = inject(ProjectLens);
  private readonly viewport = inject(ViewportService);
  private readonly key = toSignal(this.route.paramMap.pipe(map((params) => params.get('key'))), { initialValue: null });
  private readonly forgeQuery = injectForgeRecordQuery(() => this.key());
  private readonly historyQuery = injectConfigHistoryQuery('forge', () => this.forgeQuery.data()?.id ?? null);
  private readonly meQuery = injectMeQuery();
  private readonly forgeService = injectForgeService();
  private readonly lifecycleMutation = injectForgeLifecycleMutation();
  protected readonly fields = FORGE_FIELD_VIEWS;
  protected readonly vm = computed(() => forgeRecordVm(this.forgeQuery.data()));
  protected readonly state = computed<KitAsyncStateValue>(() => restingAsyncState(this.key() === null, this.forgeQuery, this.vm() === null));
  protected readonly lastChange = computed(() => lastChange(this.historyQuery.data()));
  protected readonly revisions = computed(() => revisionRows(this.historyQuery.data()));
  protected readonly revisionsState = computed<KitAsyncStateValue>(() => asyncState(this.historyQuery, this.revisions().length === 0));
  protected readonly cliCommand = computed(() => detailCliCommand(this.viewport.mode(), 'forge', this.forgeQuery.data()?.slug));
  protected readonly actions = computed(() => configActions(this.viewport.mode(), hasPermission(this.meQuery.data(), 'config:edit'), this.forgeQuery.data()));
  /** What the edit started from — `null` while no edit is under way. A refresh of the read does not replace it. */
  protected readonly editBaseline = signal<EditBaseline<ForgeDraft> | null>(null);
  private readonly editDraft = signal<ForgeDraft>(EMPTY_FORGE_DRAFT);
  protected readonly editTree = form(this.editDraft, forgeEditRules);
  private readonly pendingKey = signal<string | null>(null);
  private readonly failure = signal<ConfigFailure | null>(null);
  protected readonly busy = computed(() => this.pendingKey() !== null && this.pendingKey() === this.key());
  protected readonly actionError = computed(() => failureFor(this.failure(), this.key()));

  constructor() {
    // Selecting another forge ends the edit.
    effect(() => {
      this.key();
      untracked(() => this.editBaseline.set(null));
    });
    effect(() => {
      const forge = this.forgeQuery.data();
      if (forge && this.key() !== null && this.key() !== forge.id) {
        void this.router.navigate(this.lens.link(['/admin', 'forges', forge.id]), { replaceUrl: true, queryParamsHandling: 'preserve' });
      }
    });
  }

  protected onEdit(): void {
    const record = this.forgeQuery.data();
    if (!record) return;
    const baseline = toForgeEditBaseline(record);
    this.editTree().reset({ ...baseline.draft });
    this.editBaseline.set(baseline);
  }
  protected async onSave(): Promise<void> {
    const baseline = this.editBaseline();
    if (baseline === null) return;
    const saved = await submitForm(this.editTree, (draft) => this.forgeService.edit(baseline, draft));
    if (saved && this.editBaseline() === baseline) this.editBaseline.set(null);
  }
  protected onLifecycle(verb: 'retire' | 'enable'): void {
    const record = this.forgeQuery.data();
    if (!record) return;
    this.pendingKey.set(record.id);
    this.failure.set(null);
    this.lifecycleMutation.mutate({ forgeId: record.id, revision: record.revision, retired: verb === 'retire' }, {
      onError: (error) => this.failure.set({ key: record.id, message: errorMessage(error, `${verb} failed.`) }),
      onSettled: () => this.pendingKey.set(null),
    });
  }
}
