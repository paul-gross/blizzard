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
import { secretRows } from './secrets.model';
import { injectSecretsQuery } from './secrets.query';

/**
 * `/admin/secrets` — the secret list beside a `<router-outlet>` holding the
 * record the URL names (`secret-detail.ts`). The filter rides the query string
 * and the selection the child route. A container over the presentational
 * {@link ConfigMaster}.
 */
@Component({
  selector: 'app-secrets-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigMaster, RouterOutlet],
  templateUrl: './secrets-page.html',
  styleUrl: '../config-page-host.css',
})
export class SecretsPage {
  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);
  private readonly filters = injectQueryFilters();

  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');
  protected readonly lifecycle = computed(() => parseLifecycleFilter(this.filters.read(LIFECYCLE_FILTER_PARAM)));
  private readonly secretsQuery = injectSecretsQuery(() => includeRetired(this.lifecycle()));

  protected readonly selectedKey = injectChildRouteParam('key');
  protected readonly rows = computed<readonly ConfigRowVm[]>(() =>
    secretRows(this.secretsQuery.data() ?? [], this.lifecycle()),
  );
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.secretsQuery, this.rows().length === 0));
  protected readonly emptyText = computed(() => lifecycleEmptyText('secrets', this.lifecycle()));

  protected onFilter(value: string): void {
    this.filters.patch({ [LIFECYCLE_FILTER_PARAM]: lifecycleFilterParam(value) });
  }

  protected onPick(key: string): void {
    void this.router.navigate(['/admin', 'secrets', key], { queryParamsHandling: 'preserve' });
  }

  protected onBack(): void {
    void this.router.navigate(['/admin', 'secrets'], { queryParamsHandling: 'preserve' });
  }
}
