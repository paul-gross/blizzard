import { ChangeDetectionStrategy, Component, computed, effect, inject, signal } from '@angular/core';
import { Router, RouterOutlet } from '@angular/router';
import { form } from '@angular/forms/signals';
import {
  asyncState,
  submitForm,
  type KitAsyncStateValue,
  KitButton,
  KitMasterDetail,
  KitPanel,
  KitPanelHeader,
  ViewportService,
} from 'fleet';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { ProjectLens, lensLink } from '../../core/project-lens';
import { injectProjectDomainsQuery } from '../../core/projects.query';
import { injectChildRouteParam } from '../../core/route-state';
import { injectProjectRunnersQuery } from '../../core/runners.query';
import { ConfigFieldTreeDialog } from '../config-field-tree-dialog';
import type { ConfigFieldView } from '../config-field-tree.model';
import { projectDraft, type ProjectDraft } from './project.domain';
import { projectCreateSchema } from './project.form';
import { injectProjectService } from './project.service';
import {
  listedProjects,
  projectIdForSlug,
  projectRows,
  SLUG_HINT,
  servedProjectIds,
} from './projects.model';
import { projectToOpen, rowKeys } from './project-open.model';
import { injectProjectFigures } from './projects.query';
import { ProjectsTable } from './projects-table';

const CREATE_FIELDS: readonly ConfigFieldView<ProjectDraft>[] = [
  { key: 'slug', label: 'Slug', kind: 'text' },
  { key: 'name', label: 'Name', kind: 'text' },
  { key: 'description', label: 'Description', kind: 'text' },
];

/**
 * `/admin/projects` — the tenant's projects beside a `<router-outlet>` holding the project the
 * URL names by id (`project-detail.ts`), list and detail each a panel. On All it lists every project
 * and, on desktop, opens the first; under a project lens it lists that one project and opens it. Each row tells how the project is wired — its linked
 * sources and repositories and its queue — and what it still needs, a runner among them.
 */
@Component({
  selector: 'app-projects-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigFieldTreeDialog, KitButton, KitMasterDetail, KitPanel, KitPanelHeader, ProjectsTable, RouterOutlet],
  templateUrl: './projects-page.html',
  styleUrl: './projects-page.css',
})
export class ProjectsPage {
  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);
  private readonly lens = inject(ProjectLens);

  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');
  private readonly projectsQuery = injectProjectDomainsQuery();
  private readonly runnersQuery = injectProjectRunnersQuery();

  protected readonly selectedKey = injectChildRouteParam('key');
  private readonly lensId = computed(() => projectIdForSlug(this.projectsQuery.data(), this.lens.slug()));
  private readonly listed = computed(() => listedProjects(this.projectsQuery.data(), this.lensId()));
  private readonly figures = injectProjectFigures(() => this.listed());
  private readonly servedIds = computed(() =>
    servedProjectIds(this.projectsQuery.data() ?? [], this.runnersQuery.data()),
  );
  protected readonly rows = computed(() =>
    projectRows(this.listed(), this.figures(), this.servedIds(), this.lensId()),
  );
  protected readonly state = computed<KitAsyncStateValue>(() =>
    asyncState(this.projectsQuery, this.rows().length === 0),
  );

  private readonly meQuery = injectMeQuery();
  private readonly projectService = injectProjectService();
  protected readonly fields = CREATE_FIELDS;
  protected readonly createForm = form(signal(projectDraft(null)), projectCreateSchema);
  protected readonly slugHint = SLUG_HINT;
  protected readonly createOpen = signal(false);

  /** New is a write: a desktop with `config:edit` only. */
  protected readonly canCreate = computed(() => !this.mobile() && hasPermission(this.meQuery.data(), 'config:edit'));

  constructor() {
    // A bare Projects tab opens a project rather than an empty detail: the lensed one, else on desktop the first.
    effect(() => {
      const open = projectToOpen({
        selected: this.selectedKey(),
        lensId: this.lensId(),
        rows: rowKeys(this.rows()),
        settled: !this.projectsQuery.isPending() && !this.projectsQuery.isError(),
        mobile: this.mobile(),
      });
      if (open !== null) {
        void this.router.navigate(this.lens.link(['/admin', 'projects', open]), { replaceUrl: true });
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
    this.createForm().reset(projectDraft(null));
    this.createOpen.set(true);
  }

  protected async onCreate(): Promise<void> {
    await submitForm(this.createForm, (draft) =>
      this.projectService.create(draft, (project) => {
        this.createOpen.set(false);
        // Under a lens the new project opens in its own lens, where the list shows it.
        const commands = lensLink(['/admin', 'projects', project.project_id], this.lens.lensed() ? project.slug : null);
        void this.router.navigate(commands);
      }),
    );
  }
}
