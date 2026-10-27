import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { asyncState, errorMessage, type KitAsyncStateValue, RecordKind, restingAsyncState, ViewportService } from 'fleet';
import { map } from 'rxjs';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { ProjectLens } from '../../core/project-lens';
import { type ConfigFailure, configActions, failureFor } from '../config-actions.model';
import { detailCliCommand } from '../config-cli.model';
import { type ConfigFormValues, configEdit, shownFormValues } from '../config-edit.model';
import { ConfigFormDialog } from '../config-form-dialog';
import { lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { ConfigRecordPanel } from '../config-record-panel';
import { FORGE_FIELDS, forgeRecordVm } from './forges.model';
import { injectForgeQuery } from './forges.query';
import { injectEditForgeMutation, injectForgeLifecycleMutation } from './forges.mutations';

@Component({
  selector: 'app-forge-detail', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFormDialog, ConfigRecordPanel], templateUrl: './forge-detail.html',
})
export class ForgeDetailPage {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly lens = inject(ProjectLens);
  private readonly viewport = inject(ViewportService);
  private readonly key = toSignal(this.route.paramMap.pipe(map((params) => params.get('key'))), { initialValue: null });
  private readonly forgeQuery = injectForgeQuery(() => this.key());
  private readonly historyQuery = injectConfigHistoryQuery(RecordKind.FORGE, () => this.forgeQuery.data()?.forge_id ?? null);
  private readonly meQuery = injectMeQuery();
  protected readonly editMutation = injectEditForgeMutation();
  private readonly lifecycleMutation = injectForgeLifecycleMutation();
  protected readonly fields = FORGE_FIELDS;
  protected readonly vm = computed(() => forgeRecordVm(this.forgeQuery.data()));
  protected readonly state = computed<KitAsyncStateValue>(() => restingAsyncState(this.key() === null, this.forgeQuery, this.vm() === null));
  protected readonly lastChange = computed(() => lastChange(this.historyQuery.data()));
  protected readonly revisions = computed(() => revisionRows(this.historyQuery.data()));
  protected readonly revisionsState = computed<KitAsyncStateValue>(() => asyncState(this.historyQuery, this.revisions().length === 0));
  protected readonly cliCommand = computed(() => detailCliCommand(this.viewport.mode(), RecordKind.FORGE, this.forgeQuery.data()?.slug));
  protected readonly actions = computed(() => configActions(this.viewport.mode(), hasPermission(this.meQuery.data(), 'config:edit'), this.forgeQuery.data()));
  protected readonly shown = computed(() => shownFormValues(this.fields, this.forgeQuery.data()));
  protected readonly editOpen = signal(false);
  protected readonly editError = signal<string | null>(null);
  private readonly pendingKey = signal<string | null>(null);
  private readonly failure = signal<ConfigFailure | null>(null);
  protected readonly busy = computed(() => this.pendingKey() !== null && this.pendingKey() === this.key());
  protected readonly actionError = computed(() => failureFor(this.failure(), this.key()));

  constructor() {
    effect(() => {
      const forge = this.forgeQuery.data();
      if (forge && this.key() !== null && this.key() !== forge.forge_id) {
        void this.router.navigate(this.lens.link(['/admin', 'forges', forge.forge_id]), { replaceUrl: true, queryParamsHandling: 'preserve' });
      }
    });
  }

  protected onEdit(): void {
    this.editError.set(null);
    this.editOpen.set(true);
  }
  protected onSave(values: ConfigFormValues): void {
    const record = this.forgeQuery.data();
    const shown = this.shown();
    if (!record || !shown) return;
    this.editError.set(null);
    this.editMutation.mutate({ forgeId: record.forge_id, revision: record.revision, body: configEdit(this.fields, shown, values).body }, {
      onSuccess: () => this.editOpen.set(false),
      onError: (error) => this.editError.set(errorMessage(error, 'Save failed.')),
    });
  }
  protected onLifecycle(verb: 'retire' | 'enable'): void {
    const record = this.forgeQuery.data();
    if (!record) return;
    this.pendingKey.set(record.forge_id);
    this.failure.set(null);
    this.lifecycleMutation.mutate({ forgeId: record.forge_id, revision: record.revision, retired: verb === 'retire' }, {
      onError: (error) => this.failure.set({ key: record.forge_id, message: errorMessage(error, `${verb} failed.`) }),
      onSettled: () => this.pendingKey.set(null),
    });
  }
}
