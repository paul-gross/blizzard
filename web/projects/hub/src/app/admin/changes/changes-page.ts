import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import { asyncState, type KitAsyncStateValue, ViewportService } from 'fleet';

import { injectChildRouteParam } from '../../core/route-state';
import { changesOfPages } from '../config-history.model';
import { ConfigMaster } from '../config-master';
import type { ConfigRowVm } from '../config-record-list';
import { changeEntries, changeEntryRows } from './changes.model';
import { injectConfigChangesQuery } from './changes.query';

/**
 * `/admin/changes` — the change log across every config record, newest first, paged
 * through an Older control, beside a `<router-outlet>` holding the entry the URL
 * names (`change-detail.ts`). A container over the presentational
 * {@link ConfigMaster}.
 */
@Component({
  selector: 'app-changes-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigMaster, RouterOutlet],
  templateUrl: './changes-page.html',
  styleUrl: '../config-page-host.css',
})
export class ChangesPage {
  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);
  private readonly changesQuery = injectConfigChangesQuery();

  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');
  protected readonly selectedKey = injectChildRouteParam('key');
  protected readonly rows = computed<readonly ConfigRowVm[]>(() =>
    changeEntryRows(changeEntries(changesOfPages(this.changesQuery.data()?.pages))),
  );
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.changesQuery, this.rows().length === 0));
  protected readonly hasOlder = computed(() => this.changesQuery.hasNextPage());
  protected readonly olderPending = computed(() => this.changesQuery.isFetchingNextPage());

  protected onOlder(): void {
    void this.changesQuery.fetchNextPage();
  }

  protected onPick(key: string): void {
    void this.router.navigate(['/admin', 'changes', key]);
  }

  protected onBack(): void {
    void this.router.navigate(['/admin', 'changes']);
  }
}
