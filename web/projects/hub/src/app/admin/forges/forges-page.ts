import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import { asyncState, errorMessage, type KitAsyncStateValue, ViewportService } from 'fleet';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { ProjectLens } from '../../core/project-lens';
import { injectChildRouteParam, injectQueryFilters } from '../../core/route-state';
import { type ConfigFormValues, createBody } from '../config-edit.model';
import { ConfigFormDialog } from '../config-form-dialog';
import { includeRetired, LIFECYCLE_FILTER_PARAM, lifecycleEmptyText, lifecycleFilterParam, parseLifecycleFilter } from '../config-filter.model';
import { ConfigMaster } from '../config-master';
import { FORGE_FIELDS, forgeCreate, forgeRows } from './forges.model';
import { injectForgesQuery } from './forges.query';
import { injectCreateForgeMutation } from './forges.mutations';

@Component({
  selector: 'app-forges-page', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFormDialog, ConfigMaster, RouterOutlet],
  templateUrl: './forges-page.html', styleUrl: '../config-page-host.css',
})
export class ForgesPage {
  private readonly lens = inject(ProjectLens);
  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);
  private readonly filters = injectQueryFilters();
  private readonly meQuery = injectMeQuery();
  private readonly forgesQuery = injectForgesQuery(() => includeRetired(this.lifecycle()));
  protected readonly createMutation = injectCreateForgeMutation();
  protected readonly fields = FORGE_FIELDS;
  protected readonly selectedKey = injectChildRouteParam('key');
  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');
  protected readonly lifecycle = computed(() => parseLifecycleFilter(this.filters.read(LIFECYCLE_FILTER_PARAM)));
  protected readonly rows = computed(() => forgeRows(this.forgesQuery.data() ?? [], this.lifecycle()));
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.forgesQuery, this.rows().length === 0));
  protected readonly emptyText = computed(() => lifecycleEmptyText('forges', this.lifecycle()));
  protected readonly canCreate = computed(() => !this.mobile() && hasPermission(this.meQuery.data(), 'config:edit'));
  protected readonly createOpen = signal(false);
  protected readonly createError = signal<string | null>(null);

  protected onFilter(value: string): void {
    this.filters.patch({ [LIFECYCLE_FILTER_PARAM]: lifecycleFilterParam(value) });
  }
  protected onPick(key: string): void {
    void this.router.navigate(this.lens.link(['/admin', 'forges', key]), { queryParamsHandling: 'preserve' });
  }
  protected onBack(): void {
    void this.router.navigate(this.lens.link(['/admin', 'forges']), { queryParamsHandling: 'preserve' });
  }
  protected onNew(): void {
    this.createError.set(null);
    this.createOpen.set(true);
  }
  protected onCreate(values: ConfigFormValues): void {
    const body = forgeCreate(createBody(this.fields, values));
    this.createError.set(null);
    this.createMutation.mutate(body, {
      onSuccess: () => {
        this.createOpen.set(false);
        void this.router.navigate(this.lens.link(['/admin', 'forges', body.slug]), { queryParamsHandling: 'preserve' });
      },
      onError: (error) => this.createError.set(errorMessage(error, 'Create failed.')),
    });
  }
}
