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
import { injectRepositoryLifecycleMutation, injectEditRepositoryMutation } from './repositories.mutations';
import { type ConfigFailure, configActions, failureFor } from '../config-actions.model';
import { type ConfigFormValues, configEdit, shownFormValues } from '../config-edit.model';
import { ConfigFormDialog } from '../config-form-dialog';
import { detailCliCommand } from '../config-cli.model';
import { lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { ConfigRecordPanel } from '../config-record-panel';
import { REPOSITORY_FIELDS, repositoryRecordVm } from './repositories.model';
import { injectRepositoryQuery } from './repositories.query';

/**
 * The selected repository's detail — the right-hand child of `/admin/repositories`,
 * mounted by both its bare child (nothing selected) and `:key`. A container: it reads
 * the record and its change history and forwards plain view models to the
 * presentational {@link ConfigRecordPanel}.
 */
@Component({
  selector: 'app-repository-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFormDialog, ConfigRecordPanel],
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
  private readonly repositoryQuery = injectRepositoryQuery(() => this.key());
  private readonly historyQuery = injectConfigHistoryQuery(RecordKind.REPOSITORY, () =>
    this.repositoryQuery.data()?.repository_id ?? null,
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
    detailCliCommand(this.viewport.mode(), RecordKind.REPOSITORY, this.repositoryQuery.data()?.slug),
  );

  private readonly meQuery = injectMeQuery();
  protected readonly editMutation = injectEditRepositoryMutation();
  private readonly lifecycleMutation = injectRepositoryLifecycleMutation();

  protected readonly fields = REPOSITORY_FIELDS;
  protected readonly editOpen = signal(false);
  protected readonly editError = signal<string | null>(null);
  private readonly pendingKey = signal<string | null>(null);
  private readonly failure = signal<ConfigFailure | null>(null);

  protected readonly actions = computed(() =>
    configActions(this.viewport.mode(), hasPermission(this.meQuery.data(), 'config:edit'), this.repositoryQuery.data()),
  );
  protected readonly shown = computed(() => shownFormValues(this.fields, this.repositoryQuery.data()));
  protected readonly busy = computed(() => this.pendingKey() !== null && this.pendingKey() === this.key());
  protected readonly actionError = computed(() => failureFor(this.failure(), this.key()));

  constructor() {
    // A slug or former slug in the URL opens the repository; the URL then names it by id.
    effect(() => {
      const repository = this.repositoryQuery.data();
      const key = this.key();
      if (repository && key !== null && key !== repository.repository_id) {
        void this.router.navigate(this.lens.link(['/admin', 'repositories', repository.repository_id]), {
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
    const record = this.repositoryQuery.data();
    const shown = this.shown();
    if (!record || !shown || record.revision == null) return;
    this.editError.set(null);
    this.editMutation.mutate(
      {
        repositoryId: record.repository_id,
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
    const record = this.repositoryQuery.data();
    if (!record || record.revision == null) return;
    this.pendingKey.set(record.repository_id);
    this.failure.set(null);
    this.lifecycleMutation.mutate(
      {
        repositoryId: record.repository_id,
        revision: record.revision,
        retired: verb === 'retire',
      },
      {
        onError: (error) =>
          this.failure.set({
            key: record.repository_id,
            message: errorMessage(error, `${verb} failed.`),
          }),
        onSettled: () => this.pendingKey.set(null),
      },
    );
  }
}
