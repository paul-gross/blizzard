import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { form } from '@angular/forms/signals';
import { Router, RouterOutlet } from '@angular/router';
import { asyncState } from 'fleet/core';
import { submitForm } from 'fleet/forms';
import { type KitAsyncStateValue } from 'fleet/kit';
import { ViewportService } from 'fleet/viewport';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { injectChildRouteParam, injectQueryFilters } from '../../core/route-state';
import { ConfigFieldTreeDialog } from '../config-field-tree-dialog';
import {
  includeRetired,
  LIFECYCLE_FILTER_PARAM,
  lifecycleEmptyText,
  lifecycleFilterParam,
  parseLifecycleFilter,
} from '../config-filter.model';
import { ConfigMaster } from '../config-master';
import type { ConfigRowVm } from '../config-record-list';
import { EMPTY_SECRET_CREATE_DRAFT, type SecretCreateDraft } from './secret.domain';
import { secretCreateRules } from './secret.form';
import { injectSecretService } from './secret.service';
import { SECRET_CREATE_FIELD_VIEWS, secretRows } from './secrets.model';
import { injectSecretsQuery } from './secrets.query';
import { ProjectLens } from '../../core/project-lens';
import { LensNote } from '../../core/lens-note';

/**
 * `/admin/secrets` — the secret list beside a `<router-outlet>` holding the
 * record the URL names (`secret-detail.ts`). The filter rides the query string
 * and the selection the child route. A container over the presentational
 * {@link ConfigMaster}.
 */
@Component({
  selector: 'app-secrets-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFieldTreeDialog, ConfigMaster, LensNote, RouterOutlet],
  templateUrl: './secrets-page.html',
  styleUrl: '../config-page-host.css',
})
export class SecretsPage {
  private readonly lens = inject(ProjectLens);

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
    void this.router.navigate(this.lens.link(['/admin', 'secrets', key]), {
      queryParamsHandling: 'preserve',
    });
  }

  protected onBack(): void {
    void this.router.navigate(this.lens.link(['/admin', 'secrets']), {
      queryParamsHandling: 'preserve',
    });
  }

  private readonly meQuery = injectMeQuery();
  private readonly secretService = injectSecretService();
  protected readonly fields = SECRET_CREATE_FIELD_VIEWS;
  /** Whether the create form is open. */
  protected readonly createOpen = signal(false);
  private readonly createDraft = signal<SecretCreateDraft>(EMPTY_SECRET_CREATE_DRAFT);
  protected readonly createForm = form(this.createDraft, secretCreateRules);

  /** New is a write: a desktop with `config:edit` only. */
  protected readonly canCreate = computed(() => !this.mobile() && hasPermission(this.meQuery.data(), 'config:edit'));

  protected onNew(): void {
    this.createForm().reset({ ...EMPTY_SECRET_CREATE_DRAFT });
    this.createOpen.set(true);
  }

  /** Saves the create draft; once it settles, closes the form and opens the created secret. A refusal keeps the draft. */
  protected async onCreate(): Promise<void> {
    const name = this.createDraft().name.trim();
    if (await submitForm(this.createForm, (draft) => this.secretService.create(draft))) {
      this.createOpen.set(false);
      void this.router.navigate(this.lens.link(['/admin', 'secrets', name]), {
        queryParamsHandling: 'preserve',
      });
    }
  }
}
