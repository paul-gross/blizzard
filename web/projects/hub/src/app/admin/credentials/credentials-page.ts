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
import { injectForgesQuery } from '../forges/forges.query';
import { credentialCreate, credentialFields, credentialRows } from './credentials.model';
import { injectCreateCredentialMutation } from './credentials.mutations';
import { injectCredentialsQuery } from './credentials.query';

/**
 * `/admin/credentials` — the credential list beside a `<router-outlet>` holding the record the URL
 * names (`credential-detail.ts`). The filter rides the query string and the selection the child
 * route. A container over the presentational {@link ConfigMaster}.
 */
@Component({
  selector: 'app-credentials-page', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFormDialog, ConfigMaster, RouterOutlet],
  templateUrl: './credentials-page.html', styleUrl: '../config-page-host.css',
})
export class CredentialsPage {
  private readonly lens = inject(ProjectLens);
  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);
  private readonly filters = injectQueryFilters();
  private readonly meQuery = injectMeQuery();
  private readonly credentialsQuery = injectCredentialsQuery(() => includeRetired(this.lifecycle()));
  private readonly forgesQuery = injectForgesQuery(() => false);
  protected readonly createMutation = injectCreateCredentialMutation();
  private readonly bindings = computed(() => this.credentialsQuery.data()?.bindings ?? []);
  private readonly forgeBindings = computed(() => this.credentialsQuery.data()?.forge_bindings ?? []);
  protected readonly fields = computed(() => credentialFields(this.forgesQuery.data() ?? [], this.bindings(), this.forgeBindings()));
  protected readonly selectedKey = injectChildRouteParam('key');
  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');
  protected readonly lifecycle = computed(() => parseLifecycleFilter(this.filters.read(LIFECYCLE_FILTER_PARAM)));
  protected readonly rows = computed(() => credentialRows(this.credentialsQuery.data()?.credentials ?? [], this.lifecycle()));
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.credentialsQuery, this.rows().length === 0));
  protected readonly emptyText = computed(() => lifecycleEmptyText('credentials', this.lifecycle()));
  protected readonly canCreate = computed(() => !this.mobile() && hasPermission(this.meQuery.data(), 'config:edit'));
  protected readonly createOpen = signal(false);
  protected readonly createError = signal<string | null>(null);

  protected onFilter(value: string): void {
    this.filters.patch({ [LIFECYCLE_FILTER_PARAM]: lifecycleFilterParam(value) });
  }
  protected onPick(key: string): void {
    void this.router.navigate(this.lens.link(['/admin', 'credentials', key]), { queryParamsHandling: 'preserve' });
  }
  protected onBack(): void {
    void this.router.navigate(this.lens.link(['/admin', 'credentials']), { queryParamsHandling: 'preserve' });
  }
  protected onNew(): void {
    this.createError.set(null);
    this.createOpen.set(true);
  }
  protected onCreate(values: ConfigFormValues): void {
    const body = credentialCreate(createBody(this.fields(), values), this.bindings());
    this.createError.set(null);
    this.createMutation.mutate(body, {
      onSuccess: () => {
        this.createOpen.set(false);
        void this.router.navigate(this.lens.link(['/admin', 'credentials', body.slug]), { queryParamsHandling: 'preserve' });
      },
      onError: (error) => this.createError.set(errorMessage(error, 'Create failed.')),
    });
  }
}
