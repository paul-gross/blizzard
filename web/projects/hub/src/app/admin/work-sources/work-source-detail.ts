import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
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
import { ProjectLens } from '../../core/project-lens';
import { injectWorkSourceLifecycleMutation, injectEditWorkSourceMutation } from './work-sources.mutations';
import { type ConfigFailure, configActions, failureFor } from '../config-actions.model';
import { type ConfigFormValues, configEdit, shownFormValues } from '../config-edit.model';
import { ConfigFormDialog } from '../config-form-dialog';
import { detailCliCommand } from '../config-cli.model';
import { lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { ConfigRecordPanel } from '../config-record-panel';
import { WORK_SOURCE_FIELDS, workSourceCliName, workSourceRecordVm } from './work-sources.model';
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
  private readonly router = inject(Router);
  private readonly lens = inject(ProjectLens);
  private readonly viewport = inject(ViewportService);

  private readonly key = toSignal(this.route.paramMap.pipe(map((params) => params.get('key'))), {
    initialValue: null,
  });
  private readonly sourceQuery = injectWorkSourceQuery(() => this.key());
  private readonly historyQuery = injectConfigHistoryQuery(RecordKind.WORK_SOURCE, () =>
    this.sourceQuery.data()?.work_source_id ?? null,
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
    detailCliCommand(this.viewport.mode(), RecordKind.WORK_SOURCE, workSourceCliName(this.sourceQuery.data())),
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

  constructor() {
    // A slug or former slug in the URL opens the work source; the URL then names it by id.
    effect(() => {
      const source = this.sourceQuery.data();
      const key = this.key();
      const id = source?.work_source_id;
      if (id && key !== null && key !== id) {
        void this.router.navigate(this.lens.link(['/admin', 'work-sources', id]), {
          replaceUrl: true,
          queryParamsHandling: 'preserve',
        });
      }
    });
  }

  protected onEdit(): void {
    this.editError.set(null);
    this.editOpen.set(true);
  }

  protected onSave(values: ConfigFormValues): void {
    const record = this.sourceQuery.data();
    const shown = this.shown();
    const id = record?.work_source_id;
    if (!record || !id || !shown || record.revision == null) return;
    this.editError.set(null);
    this.editMutation.mutate(
      {
        workSourceId: id,
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
    const id = record?.work_source_id;
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
