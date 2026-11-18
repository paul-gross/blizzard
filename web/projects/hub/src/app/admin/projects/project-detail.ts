import { ChangeDetectionStrategy, Component, computed, effect, inject, linkedSignal, signal } from '@angular/core';
import { apply, form } from '@angular/forms/signals';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import {
  asyncState,
  createRefusalState,
  asyncStateOf,
  errorMessage,
  submitForm,
  type EditBaseline,
  injectNowSignal,
  injectPendingMutationVariables,
  type KitAsyncStateValue,
  KitAsyncState,
  KitButton,
  KitDialog,
  KitErrorLine,
  type KitTabOption,
  restingAsyncState,
  ViewportService,
} from 'fleet';
import { map } from 'rxjs';

import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { landingPositionMutationKey, projectLinkMutationKey } from '../../core/mutation-keys';
import { startOfLocalDayIso } from '../../core/local-day';
import { ProjectLens, lensLink } from '../../core/project-lens';
import { injectProjectRunnersQuery } from '../../core/runners.query';
import { ConfigFieldTreeDialog } from '../config-field-tree-dialog';
import type { ConfigFieldView } from '../config-field-tree.model';
import { lastChange, revisionRows } from '../config-history.model';
import { injectProjectHistoryQuery } from '../config-history.query';
import { injectProjectRepositoriesQuery } from '../repositories/repositories.query';
import { injectProjectWorkSourcesQuery } from '../work-sources/work-sources.query';
import { projectDraft, type ProjectDraft } from './project.domain';
import { projectLinkChoices, projectLinkDraft, type ProjectLinkDraft } from './project-link.domain';
import { projectLinkSchema } from './project-link.form';
import { injectProjectLinkService } from './project-link.service';
import { projectBranchBaseline, type ProjectBranchBaseline, type ProjectBranchDraft } from './project-branch.domain';
import { injectProjectBranchService } from './project-branch.service';
import { branchDialogSchema } from './project-branch.form';
import { projectEditSchema } from './project.form';
import { injectProjectService } from './project.service';
import { ProjectColorPicker } from './project-color-picker';
import { PROJECT_COLORS } from './project-color.translate';
import type { LandingPosition } from './project-landing-order.domain';
import {
  foldLandingPositions,
  pendingRepositionKeys,
  landingSections,
  type LandingPositionVars,
} from './project-landing-order.model';
import { ProjectBranchDialog } from './project-branch-dialog';
import { ProjectLinkDialog } from './project-link-dialog';
import { ProjectRecordPanel, type ProjectTab, type ProjectUnlinkEvent } from './project-record-panel';
import {
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
  type ProjectLinkRowVm,
  projectServedBy,
  repositoryChoices,
  repositoryLinkRows,
  SLUG_HINT,
  sourceChoices,
  sourceLinkRows,
} from './projects.model';
import { linkCount, parseProjectTab, projectTabs, servedByLines } from './project-overview.model';
import {
  injectProjectChunkCountsQuery,
  injectProjectQuery,
  injectProjectRepositoryLinksQuery,
  injectProjectSourceLinksQuery,
} from './projects.query';

@Component({
  selector: 'app-project-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ConfigFieldTreeDialog,
    KitAsyncState,
    KitButton,
    KitDialog,
    KitErrorLine,
    ProjectBranchDialog,
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
  private readonly sourcesQuery = injectProjectWorkSourcesQuery(() => true);
  private readonly repositoriesQuery = injectProjectRepositoriesQuery(() => true);
  private readonly runnersQuery = injectProjectRunnersQuery();
  private readonly historyQuery = injectProjectHistoryQuery(() => this.projectId());
  private readonly now = injectNowSignal();
  private readonly dayStart = computed(() => startOfLocalDayIso(this.now()));
  protected readonly countsQuery = injectProjectChunkCountsQuery(
    () => this.project()?.slug ?? null,
    () => this.dayStart(),
  );

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

  protected readonly canEdit = computed(
    () => this.viewport.mode() !== 'mobile' && hasPermission(this.meQuery.data(), 'config:edit'),
  );

  protected readonly projectService = injectProjectService();
  protected readonly linkMutation = injectProjectLinkMutation();
  private readonly positionMutation = injectLandingPositionMutation();
  protected readonly linkService = injectProjectLinkService();
  protected readonly branchService = injectProjectBranchService();
  private readonly linkDraft = signal<ProjectLinkDraft>(projectLinkDraft([]));
  private readonly linkRefusal = createRefusalState<ProjectLinkDraft>();
  protected readonly linkForm = form(this.linkDraft, (p) => {
    apply(p, projectLinkSchema(() => this.linkChoices()));
    apply(p, this.linkRefusal.schema);
  });
  private readonly linkProjectId = signal<string | null>(null);
  protected readonly linkOpeningKey = signal<string | null>(null);
  private linkChoiceInitialized = false;
  private readonly branchDraft = signal<ProjectBranchDraft>({ baseBranch: '' });
  private readonly inheriting = signal(false);
  private readonly branchRefusal = createRefusalState<ProjectBranchDraft>();
  protected readonly branchForm = form(this.branchDraft, (p) => {
    apply(p, this.branchRefusal.schema);
    apply(p, branchDialogSchema(() => this.inheriting()));
  });
  private readonly branchBaseline = signal<ProjectBranchBaseline | null>(null);
  protected readonly branchRow = signal<ProjectLinkRowVm | null>(null);
  protected readonly branchProjectName = signal('');
  protected readonly fields: readonly ConfigFieldView<ProjectDraft>[] = [
    { key: 'slug', label: 'Slug', kind: 'text' },
    { key: 'name', label: 'Name', kind: 'text' },
    { key: 'description', label: 'Description', kind: 'text' },
  ];
  protected readonly slugHint = SLUG_HINT;
  private readonly editDraft = signal<ProjectDraft>(projectDraft(null));
  private readonly editRefusal = createRefusalState<ProjectDraft>();
  protected readonly editForm = form(this.editDraft, (p) => {
    apply(p, projectEditSchema);
    apply(p, this.editRefusal.schema);
  });
  protected readonly editBaseline = signal<EditBaseline<ProjectDraft> | null>(null);
  protected readonly palette: readonly string[] = PROJECT_COLORS;
  protected readonly colorOpen = signal(false);
  protected readonly colorError = signal<string | null>(null);
  protected readonly editOpen = signal(false);

  protected readonly linkOpen = signal<ProjectLinkKind | null>(null);
  private readonly linkFailure = signal<PanelFailure<ProjectLinkKind> | null>(null);
  private readonly pendingLinks = injectPendingMutationVariables<ProjectLinkVars>(projectLinkMutationKey);
  protected readonly pendingSourceKeys = computed(() =>
    pendingUnlinkNames(this.pendingLinks(), this.projectId(), 'source'),
  );
  protected readonly pendingRepositoryKeys = computed(() =>
    pendingUnlinkNames(this.pendingLinks(), this.projectId(), 'repository'),
  );
  protected readonly pendingRepositionKeys = computed(() =>
    pendingRepositionKeys(this.pendingPositions(), this.projectId()),
  );
  protected readonly sourceError = computed(() => failureFor(this.linkFailure(), 'source'));
  protected readonly repositoryError = computed(() => failureFor(this.linkFailure(), 'repository'));

  protected readonly sourceChoices = computed(() =>
    sourceChoices(this.sourcesQuery.data(), this.sourceLinksQuery.data()),
  );
  protected readonly repositoryChoices = computed(() =>
    repositoryChoices(this.repositoriesQuery.data(), this.repositoryLinksQuery.data()),
  );

  private readonly linkChoices = computed(() => projectLinkChoices(this.linkOpen(), this.sourceChoices(), this.repositoryChoices()));

  constructor() {
    effect(() => {
      const choices = this.linkChoices();
      if (this.linkOpen() && !this.linkChoiceInitialized && choices.length > 0) {
        this.linkChoiceInitialized = true;
        if (!this.linkDraft().name) this.linkForm().reset(projectLinkDraft(choices));
      }
    });
    effect(() => {
      const key = this.key();
      if (this.linkOpen() && key !== this.linkOpeningKey()) this.linkOpen.set(null);
    });
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
    const tab = parseProjectTab(value);
    if (tab !== null) this.tab.set(tab);
  }

  protected onEdit(): void {
    const project = this.project();
    if (!project || !this.canEdit()) return;
    const draft = projectDraft(project);
    this.editBaseline.set({ id: project.project_id, revision: project.revision, draft });
    this.editRefusal.clear();
    this.editForm().reset({ ...draft });
    this.editOpen.set(true);
  }

  protected onColorOpen(): void {
    this.colorError.set(null);
    this.colorOpen.set(true);
  }

  protected onColorClose(): void {
    if (this.projectService.editPending()) return;
    this.colorOpen.set(false);
  }

  protected async onColor(color: string): Promise<void> {
    const project = this.project();
    if (!project || color === project.color || !this.canEdit()) return;
    this.colorError.set(null);
    const outcome = await this.projectService.color(project.project_id, project.revision, color);
    if (outcome.ok) this.colorOpen.set(false);
    else this.colorError.set(outcome.form ?? (Object.values(outcome.fields).join('; ') || 'Save failed.'));
  }

  protected async onSave(): Promise<void> {
    const baseline = this.editBaseline();
    if (!baseline || !this.canEdit()) return;
    await submitForm(this.editForm, (draft) => this.projectService.edit(baseline, draft, (saved) => {
      this.editOpen.set(false);
      if (this.projectId() === baseline.id && this.lens.slug() === baseline.draft.slug && saved.slug !== baseline.draft.slug) {
        void this.router.navigate(lensLink(['/admin', 'projects', saved.project_id], saved.slug), {
          replaceUrl: true,
        });
      }
    }), { refusalState: this.editRefusal });
  }

  protected onLinkOpen(kind: ProjectLinkKind): void {
    const id = this.projectId();
    if (id === null || !this.canEdit()) return;
    this.linkProjectId.set(id);
    this.linkOpeningKey.set(this.key());
    this.linkOpen.set(kind);
    const choices = this.linkChoices();
    this.linkChoiceInitialized = choices.length > 0;
    this.linkRefusal.clear();
    this.linkForm().reset(projectLinkDraft(choices));
  }

  protected async onLink(): Promise<void> {
    const id = this.linkProjectId();
    const kind = this.linkOpen();
    if (id === null || kind === null || this.key() !== this.linkOpeningKey() || !this.canEdit()) return;
    const ok = await submitForm(this.linkForm, (draft) => this.linkService.link(id, kind, draft, this.linkChoices()), { refusalState: this.linkRefusal });
    if (ok) this.linkOpen.set(null);
  }

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

  protected onBranchOpen(row: ProjectLinkRowVm): void {
    const link = this.repositoryLinksQuery.data()?.find((item) => item.repository_id === row.key);
    if (!link || !row.branch || !this.canEdit()) return;
    const baseline = projectBranchBaseline(link);
    this.branchBaseline.set(baseline);
    this.branchProjectName.set(this.project()!.name);
    this.branchRefusal.clear();
    this.branchForm().reset({ ...baseline.draft });
    this.branchRow.set(row);
  }

  protected onBranchClose(): void {
    if (this.branchService.pending()) return;
    this.branchRow.set(null);
  }

  protected async onBranch(): Promise<void> {
    const baseline = this.branchBaseline();
    if (!baseline || !this.canEdit()) return;
    const ok = await submitForm(this.branchForm, (draft) => this.branchService.save(baseline, draft), { refusalState: this.branchRefusal });
    if (ok) this.branchRow.set(null);
  }

  protected async onInherit(): Promise<void> {
    const baseline = this.branchBaseline();
    if (!baseline || !this.canEdit() || this.branchService.pending()) return;
    this.inheriting.set(true);
    try {
      const ok = await submitForm(this.branchForm, () => this.branchService.inherit(baseline), { ignoreLocalErrors: true, refusalState: this.branchRefusal });
      if (ok) this.branchRow.set(null);
    } finally { this.inheriting.set(false); }
  }

  protected async onReposition(body: LandingPosition): Promise<void> {
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
