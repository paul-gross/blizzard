import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { asyncState, type KitAsyncStateValue, RecordKind, restingAsyncState, ViewportService } from 'fleet';
import { map } from 'rxjs';

import { detailCliCommand } from '../config-cli.model';
import { historyRecordKey, lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { ConfigRecordPanel } from '../config-record-panel';
import { secretRecordVm } from './secrets.model';
import { injectSecretQuery } from './secrets.query';

/**
 * The selected secret's detail — the right-hand child of `/admin/secrets`,
 * mounted by both its bare child (nothing selected) and `:key`. A container: it reads
 * the record and its change history and forwards plain view models to the
 * presentational {@link ConfigRecordPanel}.
 */
@Component({
  selector: 'app-secret-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigRecordPanel],
  templateUrl: './secret-detail.html',
})
export class SecretDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly viewport = inject(ViewportService);

  private readonly key = toSignal(this.route.paramMap.pipe(map((params) => params.get('key'))), {
    initialValue: null,
  });
  private readonly secretQuery = injectSecretQuery(() => this.key());
  private readonly historyQuery = injectConfigHistoryQuery(RecordKind.SECRET, () =>
    historyRecordKey(this.secretQuery.data()),
  );

  protected readonly vm = computed(() => secretRecordVm(this.secretQuery.data()));
  protected readonly state = computed<KitAsyncStateValue>(() =>
    restingAsyncState(this.key() === null, this.secretQuery, this.vm() === null),
  );
  protected readonly lastChange = computed(() => lastChange(this.historyQuery.data()));
  protected readonly revisions = computed(() => revisionRows(this.historyQuery.data()));
  protected readonly revisionsState = computed<KitAsyncStateValue>(() =>
    asyncState(this.historyQuery, this.revisions().length === 0),
  );
  protected readonly cliCommand = computed(() =>
    detailCliCommand(this.viewport.mode(), RecordKind.SECRET, this.secretQuery.data()),
  );
}
