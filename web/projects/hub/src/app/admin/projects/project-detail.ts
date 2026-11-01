import {
  ChangeDetectionStrategy,
  Component,
  computed,
  effect,
  inject,
  linkedSignal,
  signal,
} from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import {
  asyncState,
  asyncStateOf,
  errorMessage,
  injectNowSignal,
  injectPendingMutationVariables,
  type KitAsyncStateValue,
  KitAsyncState,
  KitButton,
  KitDialog,
  KitErrorLine,
  type KitTabOption,
  type LandingPositionRequest,
  ProjectColor,
  RecordKind,
  restingAsyncState,
  ViewportService,
} from 'fleet';
import { map } from 'rxjs';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { landingPositionMutationKey, projectLinkMutationKey } from '../../core/mutation-keys';
import { startOfLocalDayIso } from '../../core/local-day';
import { ProjectLens, lensLink } from '../../core/project-lens';
import { injectHubRunnersQuery } from '../../core/runners.query';
import { type ConfigFormValues, configEdit } from '../config-edit.model';
import { ConfigFormDialog } from '../config-form-dialog';
import { lastChange, revisionRows } from '../config-history.model';
import { injectConfigHistoryQuery } from '../config-history.query';
import { injectRepositoriesQuery } from '../repositories/repositories.query';
import { injectWorkSourcesQuery } from '../work-sources/work-sources.query';
import { ProjectColorPicker } from './project-color-picker';
import {
  foldLandingPositions,
  landingSections,
  type LandingPositionVars,
} from './project-landing-order.model';
import { ProjectLinkDialog } from './project-link-dialog';
import { ProjectRecordPanel, type ProjectTab, type ProjectUnlinkEvent } from './project-record-panel';
import {
  injectEditProjectMutation,
  injectLandingPositionMutation,
  injectProjectLinkMutation,
  type ProjectLinkKind,
  type ProjectLinkVars,
} from './projects.mutations';
import {
  failureFor,
  formerSlugs,
  overviewCallout,
  pendingUnlinkNames,
  type PanelFailure,
  PROJECT_FIELDS,
  projectFormValues,
  projectPatch,
  projectServedBy,
  repositoryChoices,
  repositoryLinkRows,
  SLUG_HINT,
  sourceChoices,
  sourceLinkRows,
} from './projects.model';
import { linkCount, projectTabs, servedByLines } from './project-overview.model';
import {
  injectProjectChunkCountsQuery,
  injectProjectQuery,
  injectProjectRepositoryLinksQuery,
  injectProjectSourceLinksQuery,
} from './projects.query';

/**
 * The selected project — the right-hand child of `/admin/projects`, mounted by both its bare
 * child (nothing selected) and `:key`. The key is the project's id; an older link naming a
 * slug or a former slug still opens it, and the URL is rewritten to the id. Its Overview tab
 * answers "is this project wired?": where its work comes from, where it may land, who serves
 * it — with a callout for what is missing — each card opening the tab that edits it; the
 * Linked sources and Linked repositories tabs link and unlink.
 */
@Component({
  selector: 'app-project-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ConfigFormDialog,
    KitAsyncState,
    KitButton,
    KitDialog,
    KitErrorLine,
    ProjectColorPicker,
    ProjectLinkDialog,
    ProjectRecordPanel,
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
  private readonly now = injectNowSignal();
  /** Today's boundary, cut from the shared clock; string equality stops the tick here, so the key changes only when the local day does. */
  private readonly dayStart = computed(() => startOfLocalDayIso(this.now()));
  protected readonly countsQuery = injectProjectChunkCountsQuery(
    () => this.project()?.slug ?? null,
    () => this.dayStart(),
  );

  /** The open tab — Overview again whenever another project opens. */
  protected readonly tab = linkedSignal<string | null, ProjectTab>({
    source: this.projectId,
    computation: () => 'overview',
  });

  protected readonly state = computed<KitAsyncStateValue>(() =>
    restingAsyncState(this.key() === null, this.projectQuery, this.project() === null),
  );

  protected readonly sourceRows = computed(() =>
    sourceLinkRows(this.sourceLinksQuery.data() ?? [], this.sourcesQuery.data() ?? []),
  );
  protected readonly repositoryRows = computed(() =>
    repositoryLinkRows(this.repositoryLinksQuery.data() ?? [], this.repositoriesQuery.data() ?? []),
  );
  private readonly pendingPositions = injectPendingMutationVariables<LandingPositionVars>(landingPositionMutationKey);
  /** The repository rows as a landing order, each pending move folded in so it renders before it settles. */
  protected readonly landingOrder = computed(() =>
    foldLandingPositions(
      landingSections(this.repositoryRows(), this.repositoryLinksQuery.data() ?? []),
      this.pendingPositions(),
      this.projectId(),
    ),
  );
  protected readonly servedBy = computed(() =>
    projectServedBy(this.projectId(), this.runnersQuery.data()),
  );
  protected readonly servedLines = computed(() => servedByLines(this.servedBy()));
  /** Each list rests on its own links read and its catalogue; the empty copy waits for both. */
  protected readonly sourcesState = computed<KitAsyncStateValue>(() =>
    asyncStateOf([this.sourceLinksQuery, this.sourcesQuery], this.sourceRows().length === 0),
  );
  protected readonly repositoriesState = computed<KitAsyncStateValue>(() =>
    asyncStateOf([this.repositoryLinksQuery, this.repositoriesQuery], this.repositoryRows().length === 0),
  );
  protected readonly servedState = computed<KitAsyncStateValue>(() =>
    asyncState(this.runnersQuery, this.servedLines().length === 0),
  );
  protected readonly tabs = computed<readonly KitTabOption[]>(() =>
    projectTabs(linkCount(this.sourceLinksQuery.data()), linkCount(this.repositoryLinksQuery.data())),
  );
  private readonly runnersRead = computed(() => this.runnersQuery.data() !== undefined);
  private readonly served = computed(() => this.servedBy().length > 0);
  protected readonly callout = computed(() =>
    overviewCallout(
      this.sourceLinksQuery.data(),
      this.repositoryLinksQuery.data(),
      this.runnersRead(),
      this.served(),
      this.countsQuery.data()?.ready ?? null,
    ),
  );
  protected readonly formerSlugs = computed(() => formerSlugs(this.historyQuery.data(), this.project()));
  protected readonly lastChange = computed(() => lastChange(this.historyQuery.data()));
  protected readonly revisions = computed(() => revisionRows(this.historyQuery.data()));
  protected readonly revisionsState = computed<KitAsyncStateValue>(() =>
    asyncState(this.historyQuery, this.revisions().length === 0),
  );

  private readonly meQuery = injectMeQuery();

  /** Writes are a desktop with `config:edit`. */
  protected readonly canEdit = computed(
    () => this.viewport.mode() !== 'mobile' && hasPermission(this.meQuery.data(), 'config:edit'),
  );

  protected readonly editMutation = injectEditProjectMutation();
  protected readonly linkMutation = injectProjectLinkMutation();
  private readonly positionMutation = injectLandingPositionMutation();
  protected readonly fields = PROJECT_FIELDS;
  protected readonly slugHint = SLUG_HINT;
  protected readonly shown = computed(() => projectFormValues(this.project()));
  /** The palette, by name, read from the generated enum. */
  protected readonly palette: readonly string[] = Object.values(ProjectColor);
  protected readonly colorOpen = signal(false);
  protected readonly colorError = signal<string | null>(null);
  protected readonly editOpen = signal(false);
  protected readonly editError = signal<string | null>(null);

  protected readonly linkOpen = signal<ProjectLinkKind | null>(null);
  protected readonly linkError = signal<string | null>(null);
  private readonly linkFailure = signal<PanelFailure<ProjectLinkKind> | null>(null);
  private readonly pendingLinks = injectPendingMutationVariables<ProjectLinkVars>(projectLinkMutationKey);
  /** Every unlink in flight, per kind, so concurrent unlinks each keep their row waiting. */
  protected readonly pendingSourceKeys = computed(() =>
    pendingUnlinkNames(this.pendingLinks(), this.projectId(), 'source'),
  );
  protected readonly pendingRepositoryKeys = computed(() =>
    pendingUnlinkNames(this.pendingLinks(), this.projectId(), 'repository'),
  );
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

  protected onTab(value: string): void {
    if (value === 'overview' || value === 'sources' || value === 'repositories') this.tab.set(value);
  }

  protected onEdit(): void {
    this.editError.set(null);
    this.editOpen.set(true);
  }

  protected onColorOpen(): void {
    this.colorError.set(null);
    this.colorOpen.set(true);
  }

  protected onColorClose(): void {
    if (this.editMutation.isPending()) return;
    this.colorOpen.set(false);
  }

  /** Save the chosen color against the revision on screen; a stale revision or a refusal shows in the dialog. */
  protected onColor(color: string): void {
    const project = this.project();
    if (!project || color === project.color) return;
    this.colorError.set(null);
    this.editMutation.mutate(
      { projectId: project.project_id, revision: project.revision, body: { color } },
      {
        onSuccess: () => this.colorOpen.set(false),
        onError: (error) => this.colorError.set(errorMessage(error, 'Save failed.')),
      },
    );
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

  protected onLink(kind: ProjectLinkKind, key: string): void {
    const projectId = this.projectId();
    if (projectId === null) return;
    this.linkError.set(null);
    this.linkMutation.mutate(
      { projectId, kind, name: key, linked: true, revision: null },
      {
        onSuccess: () => this.linkOpen.set(null),
        onError: (error) => this.linkError.set(errorMessage(error, 'Link failed.')),
      },
    );
  }

  /** Each unlink settles on its own promise: a per-call `mutate` callback fires only for the observer's latest call. */
  protected async onUnlink({ kind, row }: ProjectUnlinkEvent): Promise<void> {
    const projectId = this.projectId();
    if (projectId === null) return;
    this.linkFailure.set(null);
    try {
      await this.linkMutation.mutateAsync({ projectId, kind, name: row.key, linked: false, revision: row.revision });
    } catch (error) {
      this.linkFailure.set({ kind, message: errorMessage(error, 'Unlink failed.') });
    }
  }

  /** One drop's landing position; a refusal shows on the repositories panel's error line. */
  protected async onReposition(body: LandingPositionRequest): Promise<void> {
    const projectId = this.projectId();
    if (projectId === null) return;
    this.linkFailure.set(null);
    try {
      await this.positionMutation.mutateAsync({ projectId, body });
    } catch (error) {
      this.linkFailure.set({ kind: 'repository', message: errorMessage(error, 'Reorder failed.') });
    }
  }
}
