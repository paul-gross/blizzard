import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { asyncState, type KitAsyncStateValue, RecordKind, restingAsyncState, ViewportService } from 'fleet';
import { map } from 'rxjs';

import { detailCliCommand } from '../config-cli.model';
import { historyRecordKey, lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { ConfigRecordPanel } from '../config-record-panel';
import { repositoryRecordVm } from './repositories.model';
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
  imports: [ConfigRecordPanel],
  templateUrl: './repository-detail.html',
})
export class RepositoryDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly viewport = inject(ViewportService);

  private readonly key = toSignal(this.route.paramMap.pipe(map((params) => params.get('key'))), {
    initialValue: null,
  });
  private readonly repositoryQuery = injectRepositoryQuery(() => this.key());
  private readonly historyQuery = injectConfigHistoryQuery(RecordKind.REPOSITORY, () =>
    historyRecordKey(this.repositoryQuery.data()),
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
    detailCliCommand(this.viewport.mode(), RecordKind.REPOSITORY, this.repositoryQuery.data()),
  );
}
