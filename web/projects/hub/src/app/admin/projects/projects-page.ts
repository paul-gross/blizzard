import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import {
  asyncState,
  errorMessage,
  type KitAsyncStateValue,
  KitButton,
  KitMasterDetail,
  ViewportService,
} from 'fleet';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { lensedProject } from '../../core/lensed-project';
import { ProjectLens, lensLink } from '../../core/project-lens';
import { injectProjectsQuery } from '../../core/projects.query';
import { injectChildRouteParam } from '../../core/route-state';
import { injectHubRunnersQuery } from '../../core/runners.query';
import { type ConfigFormValues, createBody } from '../config-edit.model';
import { ConfigFormDialog } from '../config-form-dialog';
import { injectCreateProjectMutation } from './projects.mutations';
import { PROJECT_FIELDS, projectRows, SLUG_HINT, servedProjectIds } from './projects.model';
import { ProjectsTable } from './projects-table';

/**
 * `/admin/projects` — the tenant's projects beside a `<router-outlet>` holding the project the
 * URL names by id (`project-detail.ts`). On All it lists every project; under a project lens it
 * lists that one project and opens it. A project no live runner serves carries a badge.
 */
@Component({
  selector: 'app-projects-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFormDialog, KitButton, KitMasterDetail, ProjectsTable, RouterOutlet],
  templateUrl: './projects-page.html',
  styleUrl: './projects-page.css',
})
export class ProjectsPage {
  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);
  private readonly lens = inject(ProjectLens);

  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');
  private readonly projectsQuery = injectProjectsQuery();
  private readonly runnersQuery = injectHubRunnersQuery();

  protected readonly selectedKey = injectChildRouteParam('key');
  private readonly lensId = computed(
    () => lensedProject(this.projectsQuery.data(), this.lens.slug())?.project_id ?? null,
  );
  private readonly servedIds = computed(() =>
    servedProjectIds(this.projectsQuery.data() ?? [], this.runnersQuery.data()),
  );
  protected readonly rows = computed(() =>
    projectRows(this.projectsQuery.data() ?? [], this.servedIds(), this.lensId()),
  );
  protected readonly state = computed<KitAsyncStateValue>(() =>
    asyncState(this.projectsQuery, this.rows().length === 0),
  );

  private readonly meQuery = injectMeQuery();
  protected readonly createMutation = injectCreateProjectMutation();
  protected readonly fields = PROJECT_FIELDS;
  protected readonly slugHint = SLUG_HINT;
  protected readonly createOpen = signal(false);
  protected readonly createError = signal<string | null>(null);

  /** New is a write: a desktop with `config:edit` only. */
  protected readonly canCreate = computed(() => !this.mobile() && hasPermission(this.meQuery.data(), 'config:edit'));

  constructor() {
    // Under a lens the Projects tab opens the lensed project rather than an empty detail.
    effect(() => {
      const lensId = this.lensId();
      if (lensId !== null && this.selectedKey() === null) {
        void this.router.navigate(this.lens.link(['/admin', 'projects', lensId]), { replaceUrl: true });
      }
    });
  }

  protected onPick(key: string): void {
    void this.router.navigate(this.lens.link(['/admin', 'projects', key]));
  }

  protected onBack(): void {
    void this.router.navigate(this.lens.link(['/admin', 'projects']));
  }

  protected onNew(): void {
    this.createError.set(null);
    this.createOpen.set(true);
  }

  protected onCreate(values: ConfigFormValues): void {
    const body = createBody(this.fields, values);
    this.createError.set(null);
    this.createMutation.mutate(
      { slug: String(body['slug'] ?? ''), name: String(body['name'] ?? ''), description: String(body['description'] ?? '') },
      {
        onSuccess: (project) => {
          this.createOpen.set(false);
          // Under a lens the new project opens in its own lens, where the list shows it.
          const commands = lensLink(['/admin', 'projects', project.project_id], this.lens.lensed() ? project.slug : null);
          void this.router.navigate(commands);
        },
        onError: (error) => this.createError.set(errorMessage(error, 'Create failed.')),
      },
    );
  }
}
