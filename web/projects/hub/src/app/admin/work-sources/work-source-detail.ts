import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import {
  asyncState,
  errorMessage,
  type KitAsyncStateValue,
  RecordKind,
  restingAsyncState,
  ViewportService,
} from 'fleet';
import { map } from 'rxjs';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { injectWorkSourceLifecycleMutation, injectEditWorkSourceMutation } from './work-sources.mutations';
import { type ConfigFailure, configActions, failureFor } from '../config-actions.model';
import { type ConfigFormValues, configEdit, shownFormValues } from '../config-edit.model';
import { ConfigFormDialog } from '../config-form-dialog';
import { detailCliCommand } from '../config-cli.model';
import { historyRecordKey, lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { ConfigRecordPanel } from '../config-record-panel';
import { WORK_SOURCE_FIELDS, workSourceRecordVm } from './work-sources.model';
import { injectWorkSourceQuery } from './work-sources.query';

/**
 * The selected work source's detail — the right-hand child of `/admin/work-sources`,
 * mounted by both its bare child (nothing selected) and `:key`. A container: it reads
 * the source and its change history and forwards plain view models to the
 * presentational {@link ConfigRecordPanel}.
 */
@Component({
  selector: 'app-work-source-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFormDialog, ConfigRecordPanel],
  templateUrl: './work-source-detail.html',
})
export class WorkSourceDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly viewport = inject(ViewportService);

  private readonly key = toSignal(this.route.paramMap.pipe(map((params) => params.get('key'))), {
    initialValue: null,
  });
  private readonly sourceQuery = injectWorkSourceQuery(() => this.key());
  private readonly historyQuery = injectConfigHistoryQuery(RecordKind.WORK_SOURCE, () =>
    historyRecordKey(this.sourceQuery.data()),
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
    detailCliCommand(this.viewport.mode(), RecordKind.WORK_SOURCE, this.sourceQuery.data()),
  );

  private readonly meQuery = injectMeQuery();
  protected readonly editMutation = injectEditWorkSourceMutation();
  private readonly lifecycleMutation = injectWorkSourceLifecycleMutation();

  protected readonly fields = WORK_SOURCE_FIELDS;
  protected readonly editOpen = signal(false);
  protected readonly editError = signal<string | null>(null);
  private readonly pendingKey = signal<string | null>(null);
  private readonly failure = signal<ConfigFailure | null>(null);

  protected readonly actions = computed(() =>
    configActions(this.viewport.mode(), hasPermission(this.meQuery.data(), 'config:edit'), this.sourceQuery.data()),
  );
  protected readonly shown = computed(() => shownFormValues(this.fields, this.sourceQuery.data()));
  protected readonly busy = computed(() => this.pendingKey() !== null && this.pendingKey() === this.key());
  protected readonly actionError = computed(() => failureFor(this.failure(), this.key()));

  protected onEdit(): void {
    this.editError.set(null);
    this.editOpen.set(true);
  }

  protected onSave(values: ConfigFormValues): void {
    const record = this.sourceQuery.data();
    const shown = this.shown();
    if (!record || !shown || record.revision == null) return;
    this.editError.set(null);
    this.editMutation.mutate(
      {
        name: record.name,
        revision: record.revision,
        body: configEdit(this.fields, shown, values).body,
      },
      {
        onSuccess: () => this.editOpen.set(false),
        onError: (error) => this.editError.set(errorMessage(error, 'Save failed.')),
      },
    );
  }

  protected onLifecycle(verb: 'retire' | 'enable'): void {
    const record = this.sourceQuery.data();
    if (!record || record.revision == null) return;
    this.pendingKey.set(record.name);
    this.failure.set(null);
    this.lifecycleMutation.mutate(
      {
        name: record.name,
        revision: record.revision,
        retired: verb === 'retire',
      },
      {
        onError: (error) =>
          this.failure.set({
            key: record.name,
            message: errorMessage(error, `${verb} failed.`),
          }),
        onSettled: () => this.pendingKey.set(null),
      },
    );
  }
}
