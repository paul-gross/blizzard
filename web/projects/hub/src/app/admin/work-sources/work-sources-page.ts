import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import { asyncState, errorMessage, type KitAsyncStateValue, ViewportService, type WorkSourceDocument } from 'fleet';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { injectCreateWorkSourceMutation } from './work-sources.mutations';
import { injectChildRouteParam, injectQueryFilters } from '../../core/route-state';
import { type ConfigFormValues, createBody } from '../config-edit.model';
import { ConfigFormDialog } from '../config-form-dialog';
import {
  includeRetired,
  LIFECYCLE_FILTER_PARAM,
  lifecycleEmptyText,
  lifecycleFilterParam,
  parseLifecycleFilter,
} from '../config-filter.model';
import { ConfigMaster } from '../config-master';
import type { ConfigRowVm } from '../config-record-list';
import { WORK_SOURCE_FIELDS, workSourceRows } from './work-sources.model';
import { injectWorkSourcesQuery } from './work-sources.query';

/**
 * `/admin/work-sources` — the work source list beside a `<router-outlet>` holding the
 * source the URL names (`work-source-detail.ts`). The filter rides the query string
 * and the selection the child route. A container over the presentational
 * {@link ConfigMaster}.
 */
@Component({
  selector: 'app-work-sources-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFormDialog, ConfigMaster, RouterOutlet],
  templateUrl: './work-sources-page.html',
  styleUrl: '../config-page-host.css',
})
export class WorkSourcesPage {
  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);
  private readonly filters = injectQueryFilters();

  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');
  protected readonly lifecycle = computed(() => parseLifecycleFilter(this.filters.read(LIFECYCLE_FILTER_PARAM)));
  private readonly sourcesQuery = injectWorkSourcesQuery(() => includeRetired(this.lifecycle()));

  protected readonly selectedKey = injectChildRouteParam('key');
  protected readonly rows = computed<readonly ConfigRowVm[]>(() =>
    workSourceRows(this.sourcesQuery.data() ?? [], this.lifecycle()),
  );
  protected readonly state = computed<KitAsyncStateValue>(() =>
    asyncState(this.sourcesQuery, this.rows().length === 0),
  );
  protected readonly emptyText = computed(() => lifecycleEmptyText('work sources', this.lifecycle()));

  protected onFilter(value: string): void {
    this.filters.patch({
      [LIFECYCLE_FILTER_PARAM]: lifecycleFilterParam(value),
    });
  }

  protected onPick(key: string): void {
    void this.router.navigate(['/admin', 'work-sources', key], {
      queryParamsHandling: 'preserve',
    });
  }

  protected onBack(): void {
    void this.router.navigate(['/admin', 'work-sources'], {
      queryParamsHandling: 'preserve',
    });
  }

  private readonly meQuery = injectMeQuery();
  protected readonly createMutation = injectCreateWorkSourceMutation();
  protected readonly fields = WORK_SOURCE_FIELDS;
  protected readonly createOpen = signal(false);
  protected readonly createError = signal<string | null>(null);

  /** New is a write: a desktop with `config:edit` only. */
  protected readonly canCreate = computed(() => !this.mobile() && hasPermission(this.meQuery.data(), 'config:edit'));

  protected onNew(): void {
    this.createError.set(null);
    this.createOpen.set(true);
  }

  protected onCreate(values: ConfigFormValues): void {
    const body = createBody(this.fields, values) as unknown as WorkSourceDocument;
    this.createError.set(null);
    this.createMutation.mutate(body, {
      onSuccess: () => {
        this.createOpen.set(false);
        void this.router.navigate(['/admin', 'work-sources', body.name], {
          queryParamsHandling: 'preserve',
        });
      },
      onError: (error) => this.createError.set(errorMessage(error, 'Create failed.')),
    });
  }
}
