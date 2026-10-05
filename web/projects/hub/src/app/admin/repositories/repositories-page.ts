import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import { asyncState, type KitAsyncStateValue, ViewportService } from 'fleet';

import { injectChildRouteParam, injectQueryFilters } from '../../core/route-state';
import {
  includeRetired,
  LIFECYCLE_FILTER_PARAM,
  lifecycleEmptyText,
  lifecycleFilterParam,
  parseLifecycleFilter,
} from '../config-filter.model';
import { ConfigMaster } from '../config-master';
import type { ConfigRowVm } from '../config-record-list';
import { repositoryRows } from './repositories.model';
import { injectRepositoriesQuery } from './repositories.query';

/**
 * `/admin/repositories` — the repository list beside a `<router-outlet>` holding the
 * record the URL names (`repository-detail.ts`). The filter rides the query string
 * and the selection the child route. A container over the presentational
 * {@link ConfigMaster}.
 */
@Component({
  selector: 'app-repositories-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigMaster, RouterOutlet],
  templateUrl: './repositories-page.html',
  styleUrl: '../config-page-host.css',
})
export class RepositoriesPage {
  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);
  private readonly filters = injectQueryFilters();

  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');
  protected readonly lifecycle = computed(() => parseLifecycleFilter(this.filters.read(LIFECYCLE_FILTER_PARAM)));
  private readonly repositoriesQuery = injectRepositoriesQuery(() => includeRetired(this.lifecycle()));

  protected readonly selectedKey = injectChildRouteParam('key');
  protected readonly rows = computed<readonly ConfigRowVm[]>(() =>
    repositoryRows(this.repositoriesQuery.data() ?? [], this.lifecycle()),
  );
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.repositoriesQuery, this.rows().length === 0));
  protected readonly emptyText = computed(() => lifecycleEmptyText('repositories', this.lifecycle()));

  protected onFilter(value: string): void {
    this.filters.patch({ [LIFECYCLE_FILTER_PARAM]: lifecycleFilterParam(value) });
  }

  protected onPick(key: string): void {
    void this.router.navigate(['/admin', 'repositories', key], { queryParamsHandling: 'preserve' });
  }

  protected onBack(): void {
    void this.router.navigate(['/admin', 'repositories'], { queryParamsHandling: 'preserve' });
  }
}
