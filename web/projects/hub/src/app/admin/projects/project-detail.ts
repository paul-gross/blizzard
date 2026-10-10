import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  signal,
  type TemplateRef,
  viewChild,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router, RouterLink } from '@angular/router';
import {
  asyncState,
  errorMessage,
  FleetWhen,
  type KitAsyncStateValue,
  KitAsyncState,
  KitBadge,
  KitButton,
  type KitFact,
  KitFactList,
  RecordKind,
  restingAsyncState,
  ViewportService,
} from 'fleet';
import { LensLink } from 'fleet/shell';
import { map } from 'rxjs';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { ProjectLens, lensLink } from '../../core/project-lens';
import { injectHubRunnersQuery } from '../../core/runners.query';
import { type ConfigFormValues, configEdit } from '../config-edit.model';
import { ConfigFormDialog } from '../config-form-dialog';
import { lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { ConfigRevisions } from '../config-revisions';
import { injectRepositoriesQuery } from '../repositories/repositories.query';
import { injectWorkSourcesQuery } from '../work-sources/work-sources.query';
import { ProjectLinkDialog } from './project-link-dialog';
import { ProjectLinkPanel } from './project-link-panel';
import { injectEditProjectMutation, injectProjectLinkMutation, type ProjectLinkKind } from './projects.mutations';
import {
  failureFor,
  formerSlugs,
  overviewCallout,
  type PanelFailure,
  PROJECT_FIELDS,
  projectFacts,
  type ProjectLinkRowVm,
  projectFormValues,
  projectPatch,
  projectServedBy,
  repositoryChoices,
  repositoryLinkRows,
  SLUG_HINT,
  sourceChoices,
  sourceLinkRows,
} from './projects.model';
import { injectProjectQuery, injectProjectRepositoryLinksQuery, injectProjectSourceLinksQuery } from './projects.query';

/**
 * The selected project — the right-hand child of `/admin/projects`, mounted by both its bare
 * child (nothing selected) and `:key`. The key is the project's id; an older link naming a
 * slug or a former slug still opens it, and the URL is rewritten to the id. It answers "is
 * this project wired?": where its work comes from, where it may land, who serves it — with a
 * callout for what is missing — and edits the project and its links.
 */
@Component({
  selector: 'app-project-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ConfigFormDialog,
    ConfigRevisions,
    FleetWhen,
    KitAsyncState,
    KitBadge,
    KitButton,
    KitFactList,
    LensLink,
    ProjectLinkDialog,
    ProjectLinkPanel,
    RouterLink,
  ],
  templateUrl: './project-detail.html',
  styleUrl: './project-detail.css',
})
export class ProjectDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly viewport = inject(ViewportService);
  private readonly lens = inject(ProjectLens);

  protected readonly key = toSignal(this.route.paramMap.pipe(map((params) => params.get('key'))), {
    initialValue: null,
  });
  private readonly projectQuery = injectProjectQuery(() => this.key());
  protected readonly project = computed(() => this.projectQuery.data() ?? null);
  private readonly projectId = computed(() => this.project()?.project_id ?? null);

  private readonly sourceLinksQuery = injectProjectSourceLinksQuery(() => this.projectId());
  private readonly repositoryLinksQuery = injectProjectRepositoryLinksQuery(() => this.projectId());
  private readonly sourcesQuery = injectWorkSourcesQuery(() => true);
  private readonly repositoriesQuery = injectRepositoriesQuery(() => true);
  private readonly runnersQuery = injectHubRunnersQuery();
  private readonly historyQuery = injectConfigHistoryQuery(RecordKind.PROJECT, () => this.projectId());

  protected readonly state = computed<KitAsyncStateValue>(() =>
    restingAsyncState(this.key() === null, this.projectQuery, this.project() === null),
  );

  protected readonly sourceRows = computed(() =>
    sourceLinkRows(this.sourceLinksQuery.data() ?? [], this.sourcesQuery.data() ?? []),
  );
  protected readonly repositoryRows = computed(() =>
    repositoryLinkRows(this.repositoryLinksQuery.data() ?? [], this.repositoriesQuery.data() ?? []),
  );
  protected readonly servedBy = computed(() =>
    projectServedBy(this.projectId(), this.runnersQuery.data()),
  );
  private readonly runnersRead = computed(() => this.runnersQuery.data() !== undefined);
  private readonly served = computed(() => this.servedBy().length > 0);
  protected readonly callout = computed(() =>
    overviewCallout(this.sourceLinksQuery.data(), this.repositoryLinksQuery.data(), this.runnersRead(), this.served()),
  );
  protected readonly unserved = computed(() => this.runnersRead() && !this.served());
  private readonly formerSlugs = computed(() => formerSlugs(this.historyQuery.data(), this.project()));
  protected readonly lastChange = computed(() => lastChange(this.historyQuery.data()));
  protected readonly revisions = computed(() => revisionRows(this.historyQuery.data()));
  protected readonly revisionsState = computed<KitAsyncStateValue>(() =>
    asyncState(this.historyQuery, this.revisions().length === 0),
  );

  /** Gardening under this project's own lens, whichever lens the board is on; a link already under a lens passes
   * through `lensLink` unchanged. */
  protected readonly gardeningLink = computed(() => lensLink(['/gardening'], this.project()?.slug ?? null));

  private readonly revisionTemplate = viewChild<TemplateRef<unknown>>('revisionFact');
  protected readonly facts = computed<readonly KitFact[]>(() =>
    projectFacts(this.project(), this.formerSlugs(), this.revisionTemplate()),
  );

  private readonly meQuery = injectMeQuery();
  /** Writes are a desktop with `config:edit`. */
  protected readonly canEdit = computed(
    () => this.viewport.mode() !== 'mobile' && hasPermission(this.meQuery.data(), 'config:edit'),
  );

  protected readonly editMutation = injectEditProjectMutation();
  protected readonly linkMutation = injectProjectLinkMutation();
  protected readonly fields = PROJECT_FIELDS;
  protected readonly slugHint = SLUG_HINT;
  protected readonly shown = computed(() => projectFormValues(this.project()));
  protected readonly editOpen = signal(false);
  protected readonly editError = signal<string | null>(null);

  protected readonly linkOpen = signal<ProjectLinkKind | null>(null);
  protected readonly linkError = signal<string | null>(null);
  private readonly linkFailure = signal<PanelFailure<ProjectLinkKind> | null>(null);
  protected readonly pendingUnlink = signal<string | null>(null);
  protected readonly sourceError = computed(() => failureFor(this.linkFailure(), 'source'));
  protected readonly repositoryError = computed(() => failureFor(this.linkFailure(), 'repository'));

  protected readonly sourceChoices = computed(() =>
    sourceChoices(this.sourcesQuery.data(), this.sourceLinksQuery.data()),
  );
  protected readonly repositoryChoices = computed(() =>
    repositoryChoices(this.repositoriesQuery.data(), this.repositoryLinksQuery.data()),
  );

  constructor() {
    // A slug or former slug in the URL opens the project; the URL then names it by id.
    effect(() => {
      const project = this.project();
      const key = this.key();
      if (project && key !== null && key !== project.project_id) {
        void this.router.navigate(this.lens.link(['/admin', 'projects', project.project_id]), { replaceUrl: true });
      }
    });
  }

  protected onEdit(): void {
    this.editError.set(null);
    this.editOpen.set(true);
  }

  protected onSave(values: ConfigFormValues): void {
    const project = this.project();
    if (!project) return;
    const body = projectPatch(configEdit(this.fields, this.shown(), values).body);
    this.editError.set(null);
    this.editMutation.mutate(
      { projectId: project.project_id, revision: project.revision, body },
      {
        onSuccess: (saved) => {
          this.editOpen.set(false);
          // Under this project's own lens a new slug moves the lens with it.
          if (this.lens.slug() === project.slug && saved.slug !== project.slug) {
            void this.router.navigate(lensLink(['/admin', 'projects', saved.project_id], saved.slug), {
              replaceUrl: true,
            });
          }
        },
        onError: (error) => this.editError.set(errorMessage(error, 'Save failed.')),
      },
    );
  }

  protected onLinkOpen(kind: ProjectLinkKind): void {
    this.linkError.set(null);
    this.linkOpen.set(kind);
  }

  protected onLink(kind: ProjectLinkKind, name: string): void {
    const projectId = this.projectId();
    if (projectId === null) return;
    this.linkError.set(null);
    this.linkMutation.mutate(
      { projectId, kind, name, linked: true, revision: null },
      {
        onSuccess: () => this.linkOpen.set(null),
        onError: (error) => this.linkError.set(errorMessage(error, 'Link failed.')),
      },
    );
  }

  protected onUnlink(kind: ProjectLinkKind, row: ProjectLinkRowVm): void {
    const projectId = this.projectId();
    if (projectId === null) return;
    this.linkFailure.set(null);
    this.pendingUnlink.set(row.name);
    this.linkMutation.mutate(
      { projectId, kind, name: row.name, linked: false, revision: row.revision },
      {
        onError: (error) => this.linkFailure.set({ kind, message: errorMessage(error, 'Unlink failed.') }),
        onSettled: () => this.pendingUnlink.set(null),
      },
    );
  }
}
