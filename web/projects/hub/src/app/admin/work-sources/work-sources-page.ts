import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { form } from '@angular/forms/signals';
import { Router, RouterOutlet } from '@angular/router';
import { asyncState, type KitAsyncStateValue, submitForm, ViewportService } from 'fleet';

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
import { injectLensLinks, withinLens } from '../projects/lens-links';
import { lensedListState } from '../projects/projects.model';
import type { ConfigRowVm } from '../config-record-list';
import { EMPTY_WORK_SOURCE_DRAFT, type WorkSourceDraft } from './work-source.domain';
import { workSourceCreateRules } from './work-source.form';
import { injectWorkSourceService } from './work-source.service';
import { workSourceFieldViews, workSourceKey, workSourceRows } from './work-sources.model';
import { injectWorkSourcesQuery } from './work-sources.query';
import { ProjectLens } from '../../core/project-lens';
import { credentialOptions } from '../credentials/credentials.model';
import { injectCredentialsQuery } from '../credentials/credentials.query';
import { forgeOptions } from '../forges/forges.model';
import { injectForgesQuery } from '../forges/forges.query';

/**
 * `/admin/work-sources` — the work source list beside a `<router-outlet>` holding the
 * source the URL names (`work-source-detail.ts`). The filter rides the query string
 * and the selection the child route. A container over the presentational
 * {@link ConfigMaster}.
 */
@Component({
  selector: 'app-work-sources-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFieldTreeDialog, ConfigMaster, RouterOutlet],
  templateUrl: './work-sources-page.html',
  styleUrl: '../config-page-host.css',
})
export class WorkSourcesPage {
  private readonly lens = inject(ProjectLens);

  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);
  private readonly filters = injectQueryFilters();

  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');
  protected readonly lifecycle = computed(() => parseLifecycleFilter(this.filters.read(LIFECYCLE_FILTER_PARAM)));
  /** Under a project lens the list narrows to the records that project links. */
  private readonly lensLinks = injectLensLinks('source');
  private readonly sourcesQuery = injectWorkSourcesQuery(() => includeRetired(this.lifecycle()));

  protected readonly selectedKey = injectChildRouteParam('key');
  protected readonly rows = computed<readonly ConfigRowVm[]>(() =>
    workSourceRows(withinLens(this.sourcesQuery.data() ?? [], workSourceKey, this.lensLinks.names()), this.lifecycle()),
  );
  protected readonly state = computed<KitAsyncStateValue>(() =>
    lensedListState(
      this.lensLinks.failed(),
      this.lensLinks.pending(),
      asyncState(this.sourcesQuery, this.rows().length === 0),
    ),
  );
  protected readonly caption = computed(() => this.lensLinks.caption('Work sources'));
  protected readonly emptyText = computed(() =>
    this.lensLinks.emptyText(lifecycleEmptyText('work sources', this.lifecycle()), 'work sources'),
  );

  protected onFilter(value: string): void {
    this.filters.patch({
      [LIFECYCLE_FILTER_PARAM]: lifecycleFilterParam(value),
    });
  }

  protected onPick(key: string): void {
    void this.router.navigate(this.lens.link(['/admin', 'work-sources', key]), {
      queryParamsHandling: 'preserve',
    });
  }

  protected onBack(): void {
    void this.router.navigate(this.lens.link(['/admin', 'work-sources']), {
      queryParamsHandling: 'preserve',
    });
  }

  private readonly meQuery = injectMeQuery();
  private readonly workSourceService = injectWorkSourceService();
  private readonly forgesQuery = injectForgesQuery(() => false);
  private readonly credentialsQuery = injectCredentialsQuery(() => false);
  protected readonly fields = computed(() =>
    workSourceFieldViews(
      forgeOptions(this.forgesQuery.data() ?? []),
      credentialOptions(this.credentialsQuery.data()?.credentials ?? []),
    ),
  );

  /** Whether the create form is open. */
  protected readonly createOpen = signal(false);
  private readonly createDraft = signal<WorkSourceDraft>(EMPTY_WORK_SOURCE_DRAFT);
  protected readonly createForm = form(this.createDraft, workSourceCreateRules);

  /** New is a write: a desktop with `config:edit` only. */
  protected readonly canCreate = computed(() => !this.mobile() && hasPermission(this.meQuery.data(), 'config:edit'));

  protected onNew(): void {
    this.createForm().reset({ ...EMPTY_WORK_SOURCE_DRAFT });
    this.createOpen.set(true);
  }

  /** Saves the create draft; once it settles, closes the form and opens the created work source. */
  protected async onCreate(): Promise<void> {
    const slug = this.createDraft().slug.trim();
    if (await submitForm(this.createForm, (draft) => this.workSourceService.create(draft))) {
      this.createOpen.set(false);
      void this.router.navigate(this.lens.link(['/admin', 'work-sources', slug]), {
        queryParamsHandling: 'preserve',
      });
    }
  }
}
