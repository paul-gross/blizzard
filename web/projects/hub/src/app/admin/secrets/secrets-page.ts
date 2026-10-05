import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import { asyncState, errorMessage, type KitAsyncStateValue, ViewportService } from 'fleet';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
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
import { SECRET_CREATE_FIELDS, secretRows } from './secrets.model';
import { injectCreateSecretMutation, type SecretCreateVars } from './secrets.mutations';
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
  imports: [ConfigFormDialog, ConfigMaster, RouterOutlet],
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
  protected readonly state = computed<KitAsyncStateValue>(() =>
    asyncState(this.secretsQuery, this.rows().length === 0),
  );
  protected readonly emptyText = computed(() => lifecycleEmptyText('secrets', this.lifecycle()));

  protected onFilter(value: string): void {
    this.filters.patch({
      [LIFECYCLE_FILTER_PARAM]: lifecycleFilterParam(value),
    });
  }

  protected onPick(key: string): void {
    void this.router.navigate(['/admin', 'secrets', key], {
      queryParamsHandling: 'preserve',
    });
  }

  protected onBack(): void {
    void this.router.navigate(['/admin', 'secrets'], {
      queryParamsHandling: 'preserve',
    });
  }

  private readonly meQuery = injectMeQuery();
  protected readonly createMutation = injectCreateSecretMutation();
  protected readonly fields = SECRET_CREATE_FIELDS;
  protected readonly createOpen = signal(false);
  protected readonly createError = signal<string | null>(null);

  /** New is a write: a desktop with `config:edit` only. */
  protected readonly canCreate = computed(() => !this.mobile() && hasPermission(this.meQuery.data(), 'config:edit'));

  protected onNew(): void {
    this.createError.set(null);
    this.createOpen.set(true);
  }

  protected onCreate(values: ConfigFormValues): void {
    const body = createBody(this.fields, values) as unknown as SecretCreateVars;
    this.createError.set(null);
    this.createMutation.mutate(body, {
      onSuccess: () => {
        this.createOpen.set(false);
        void this.router.navigate(['/admin', 'secrets', body.name], {
          queryParamsHandling: 'preserve',
        });
      },
      onError: (error) => this.createError.set(errorMessage(error, 'Create failed.')),
    });
  }
}
