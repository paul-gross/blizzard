import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import { type KitAsyncStateValue, restingAsyncState } from 'fleet';
import { map } from 'rxjs';

import { changesOfPages } from '../config-history.model';
import { ChangeEntryPanel } from './change-entry-panel';
import { changeDetailEmptyText, changeEntries, changeEntryVm, entryByKey } from './changes.model';
import { injectConfigChangesQuery } from './changes.query';

/**
 * The selected change log entry — the right-hand child of `/admin/changes`. The hub
 * has no single-change read, so the entry resolves off the same paged log the list
 * beside it holds (one cache entry, no second fetch); an entry on a page not yet
 * loaded reads as not found until Older reaches it.
 */
@Component({
  selector: 'app-change-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChangeEntryPanel],
  templateUrl: './change-detail.html',
})
export class ChangeDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly changesQuery = injectConfigChangesQuery();

  private readonly key = toSignal(this.route.paramMap.pipe(map((params) => params.get('key'))), {
    initialValue: null,
  });

  protected readonly vm = computed(() =>
    changeEntryVm(entryByKey(changeEntries(changesOfPages(this.changesQuery.data()?.pages)), this.key())),
  );
  protected readonly state = computed<KitAsyncStateValue>(() =>
    restingAsyncState(this.key() === null, this.changesQuery, this.vm() === null),
  );
  protected readonly emptyText = computed(() => changeDetailEmptyText(this.key()));
}
