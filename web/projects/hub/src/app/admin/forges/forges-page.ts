import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { form } from '@angular/forms/signals';
import { Router, RouterOutlet } from '@angular/router';
import { asyncState } from 'fleet/core';
import { submitForm } from 'fleet/forms';
import { type KitAsyncStateValue } from 'fleet/kit';
import { ViewportService } from 'fleet/viewport';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { ProjectLens } from '../../core/project-lens';
import { injectChildRouteParam, injectQueryFilters } from '../../core/route-state';
import { ConfigFieldTreeDialog } from '../config-field-tree-dialog';
import { includeRetired, LIFECYCLE_FILTER_PARAM, lifecycleEmptyText, lifecycleFilterParam, parseLifecycleFilter } from '../config-filter.model';
import { ConfigMaster } from '../config-master';
import { EMPTY_FORGE_DRAFT, type ForgeDraft } from '../../forge/forge.domain';
import { forgeCreateRules } from '../../forge/forge.form';
import { injectForgeService } from '../../forge/forge.service';
import { FORGE_FIELD_VIEWS, forgeRows } from './forges.model';
import { injectForgeListQuery } from './forges.query';

@Component({
  selector: 'app-forges-page', changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFieldTreeDialog, ConfigMaster, RouterOutlet],
  templateUrl: './forges-page.html', styleUrl: '../config-page-host.css',
})
export class ForgesPage {
  private readonly lens = inject(ProjectLens);
  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);
  private readonly filters = injectQueryFilters();
  private readonly meQuery = injectMeQuery();
  private readonly forgesQuery = injectForgeListQuery(() => includeRetired(this.lifecycle()));
  private readonly forgeService = injectForgeService();
  protected readonly fields = FORGE_FIELD_VIEWS;
  protected readonly selectedKey = injectChildRouteParam('key');
  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');
  protected readonly lifecycle = computed(() => parseLifecycleFilter(this.filters.read(LIFECYCLE_FILTER_PARAM)));
  protected readonly rows = computed(() => forgeRows(this.forgesQuery.data() ?? [], this.lifecycle()));
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.forgesQuery, this.rows().length === 0));
  protected readonly emptyText = computed(() => lifecycleEmptyText('forges', this.lifecycle()));
  protected readonly canCreate = computed(() => !this.mobile() && hasPermission(this.meQuery.data(), 'config:edit'));
  protected readonly createOpen = signal(false);
  private readonly createDraft = signal<ForgeDraft>(EMPTY_FORGE_DRAFT);
  protected readonly createForm = form(this.createDraft, forgeCreateRules);

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
    this.createForm().reset({ ...EMPTY_FORGE_DRAFT });
    this.createOpen.set(true);
  }
  /** Saves the create draft; once it settles, closes the dialog and opens the created forge. */
  protected async onCreate(): Promise<void> {
    const slug = this.createDraft().slug.trim();
    if (await submitForm(this.createForm, (draft) => this.forgeService.create(draft))) {
      this.createOpen.set(false);
      void this.router.navigate(this.lens.link(['/admin', 'forges', slug]), { queryParamsHandling: 'preserve' });
    }
  }
}
