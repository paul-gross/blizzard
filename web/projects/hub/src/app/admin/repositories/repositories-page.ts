import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import { form } from '@angular/forms/signals';
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
import { injectLensLinks, withinLens } from '../projects/lens-links';
import { lensedListState } from '../projects/projects.model';
import type { ConfigRowVm } from '../config-record-list';
import { honourForge, repositoryFieldViews, repositoryRows } from './repositories.model';
import { injectLandingPolicyChoicesQuery, injectRepositoryListQuery } from './repositories.query';
import { EMPTY_REPOSITORY_DRAFT, type RepositoryDraft } from './repository.domain';
import { repositoryRules } from './repository.form';
import { injectRepositoryService } from './repository.service';
import { ProjectLens } from '../../core/project-lens';
import { injectCredentialChoicesQuery } from '../credentials/credentials.query';
import { injectForgeChoicesQuery, injectForgeHonourQuery } from '../forges/forges.query';

/**
 * `/admin/repositories` — the repository list beside a `<router-outlet>` holding the
 * record the URL names (`repository-detail.ts`). The filter rides the query string
 * and the selection the child route. A container over the presentational
 * {@link ConfigMaster}.
 */
@Component({
  selector: 'app-repositories-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFieldTreeDialog, ConfigMaster, RouterOutlet],
  templateUrl: './repositories-page.html',
  styleUrl: '../config-page-host.css',
})
export class RepositoriesPage {
  private readonly lens = inject(ProjectLens);

  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);
  private readonly filters = injectQueryFilters();

  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');
  protected readonly lifecycle = computed(() => parseLifecycleFilter(this.filters.read(LIFECYCLE_FILTER_PARAM)));
  /** Under a project lens the list narrows to the records that project links. */
  private readonly lensLinks = injectLensLinks('repository');
  private readonly repositoriesQuery = injectRepositoryListQuery(() => includeRetired(this.lifecycle()));

  protected readonly selectedKey = injectChildRouteParam('key');
  protected readonly rows = computed<readonly ConfigRowVm[]>(() =>
    repositoryRows(withinLens(this.repositoriesQuery.data() ?? [], (repository) => repository.id, this.lensLinks.names()), this.lifecycle()),
  );
  protected readonly state = computed<KitAsyncStateValue>(() =>
    lensedListState(
      this.lensLinks.failed(),
      this.lensLinks.pending(),
      asyncState(this.repositoriesQuery, this.rows().length === 0),
    ),
  );
  protected readonly caption = computed(() => this.lensLinks.caption('Repositories'));
  protected readonly emptyText = computed(() =>
    this.lensLinks.emptyText(lifecycleEmptyText('repositories', this.lifecycle()), 'repositories'),
  );

  protected onFilter(value: string): void {
    this.filters.patch({
      [LIFECYCLE_FILTER_PARAM]: lifecycleFilterParam(value),
    });
  }

  protected onPick(key: string): void {
    void this.router.navigate(this.lens.link(['/admin', 'repositories', key]), {
      queryParamsHandling: 'preserve',
    });
  }

  protected onBack(): void {
    void this.router.navigate(this.lens.link(['/admin', 'repositories']), {
      queryParamsHandling: 'preserve',
    });
  }

  private readonly meQuery = injectMeQuery();
  private readonly service = injectRepositoryService();
  private readonly forgesQuery = injectForgeChoicesQuery(() => false);
  private readonly credentialsQuery = injectCredentialChoicesQuery(() => false);
  private readonly landingPoliciesQuery = injectLandingPolicyChoicesQuery();
  protected readonly createOpen = signal(false);
  private readonly draft = signal<RepositoryDraft>({ ...EMPTY_REPOSITORY_DRAFT });
  protected readonly createForm = form(this.draft, repositoryRules);
  /** The forge the create form names — its detail says which policies it can honour. */
  private readonly honourQuery = injectForgeHonourQuery(() => honourForge(this.createOpen(), this.draft()));
  protected readonly fields = computed(() =>
    repositoryFieldViews(
      this.forgesQuery.data() ?? [],
      this.credentialsQuery.data() ?? [],
      this.landingPoliciesQuery.data() ?? null,
      this.honourQuery.data() ?? null,
    ),
  );

  /** New is a write: a desktop with `config:edit` only. */
  protected readonly canCreate = computed(() => !this.mobile() && hasPermission(this.meQuery.data(), 'config:edit'));

  protected onNew(): void {
    this.createForm().reset({ ...EMPTY_REPOSITORY_DRAFT });
    this.createOpen.set(true);
  }

  protected async onCreate(): Promise<void> {
    const slug = this.createForm.slug().value().trim();
    if (!(await submitForm(this.createForm, (draft) => this.service.create(draft)))) return;
    this.createOpen.set(false);
    void this.router.navigate(this.lens.link(['/admin', 'repositories', slug]), {
      queryParamsHandling: 'preserve',
    });
  }
}
