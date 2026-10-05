import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { asyncState, type KitAsyncStateValue, RecordKind, restingAsyncState, ViewportService } from 'fleet';
import { map } from 'rxjs';

import { detailCliCommand } from '../config-cli.model';
import { historyRecordKey, lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { ConfigRecordPanel } from '../config-record-panel';
import { workSourceRecordVm } from './work-sources.model';
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
  imports: [ConfigRecordPanel],
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
}
