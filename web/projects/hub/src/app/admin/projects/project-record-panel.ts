import { ChangeDetectionStrategy, Component, computed, input, output, viewChild, type TemplateRef } from '@angular/core';
import {
  type ChunkCountsView,
  FleetWhen,
  type KitAsyncStateValue,
  KitButton,
  type KitFact,
  KitFactList,
  KitSwatch,
  type KitTabOption,
  KitTabs,
  type ProjectView,
} from 'fleet';

import { type LastChangeVm, type RevisionRowVm } from '../config-history.model';
import { ConfigRevisions } from '../config-revisions';
import { ProjectLinkPanel } from './project-link-panel';
import { ProjectOverviewCard } from './project-overview-card';
import { projectFacts, type OverviewLineVm } from './project-overview.model';
import type { ProjectLinkKind } from './projects.mutations';
import type { ProjectLinkRowVm } from './projects.model';

/** The project page's tabs: what it is and who serves it, then each kind of link it holds. */
export type ProjectTab = 'overview' | 'sources' | 'repositories';

/** An Unlink asked for on one row of one kind's panel. */
export interface ProjectUnlinkEvent {
  readonly kind: ProjectLinkKind;
  readonly row: ProjectLinkRowVm;
}

/**
 * The selected project's record — its header (swatch, name, description, Color and Edit), the tab strip, and the
 * Overview, Linked sources and Linked repositories bodies. Presentational: the project, its reads' view models and
 * states, the pending unlinks and the panels' failures in; edit, color, tab, link and unlink out.
 */
@Component({
  selector: 'app-project-record-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ConfigRevisions, FleetWhen, KitButton, KitFactList, KitSwatch, KitTabs, ProjectLinkPanel, ProjectOverviewCard],
  templateUrl: './project-record-panel.html',
  styleUrl: './project-record-panel.css',
})
export class ProjectRecordPanel {
  readonly project = input.required<ProjectView>();
  /** Whether the viewer may edit the project and its links. */
  readonly canEdit = input(false);
  readonly tabs = input.required<readonly KitTabOption[]>();
  readonly tab = input.required<ProjectTab>();
  /** The slugs the project left, newest first. */
  readonly formerSlugs = input<readonly string[]>([]);
  readonly counts = input<ChunkCountsView | undefined>(undefined);
  readonly lastChange = input<LastChangeVm | null>(null);
  /** What the project still lacks, or `null` when it is wired. */
  readonly callout = input<string | null>(null);

  readonly sourceRows = input.required<readonly ProjectLinkRowVm[]>();
  readonly repositoryRows = input.required<readonly ProjectLinkRowVm[]>();
  readonly servedLines = input.required<readonly OverviewLineVm[]>();
  /** What each list rests on; the empty copy shows only once its reads settled empty. */
  readonly sourcesState = input<KitAsyncStateValue>('ready');
  readonly repositoriesState = input<KitAsyncStateValue>('ready');
  readonly servedState = input<KitAsyncStateValue>('ready');
  readonly revisions = input.required<readonly RevisionRowVm[]>();
  readonly revisionsState = input<KitAsyncStateValue>('ready');

  /** The keys of the links an unlink is in flight for, per kind. */
  readonly pendingSourceKeys = input<ReadonlySet<string>>(new Set());
  readonly pendingRepositoryKeys = input<ReadonlySet<string>>(new Set());
  readonly sourceError = input<string | null>(null);
  readonly repositoryError = input<string | null>(null);

  readonly edit = output<void>();
  readonly color = output<void>();
  readonly tabChange = output<string>();
  readonly link = output<ProjectLinkKind>();
  readonly unlink = output<ProjectUnlinkEvent>();

  private readonly createdTemplate = viewChild<TemplateRef<unknown>>('createdFact');
  private readonly colorTemplate = viewChild<TemplateRef<unknown>>('colorFact');
  private readonly revisionTemplate = viewChild<TemplateRef<unknown>>('revisionFact');
  protected readonly facts = computed<readonly KitFact[]>(() =>
    projectFacts(this.project(), {
      former: this.formerSlugs(),
      counts: this.counts(),
      color: this.colorTemplate(),
      created: this.createdTemplate(),
      revision: this.revisionTemplate(),
    }),
  );
}
