import { ChangeDetectionStrategy, Component, type TemplateRef, computed, input, output, viewChild } from '@angular/core';
import { RouterLink } from '@angular/router';

import {
  ChunkDelivery,
  type ChunkDetail,
  ChunkFacts,
  ChunkIssuePane,
  KitAsyncState,
  type KitAsyncStateValue,
  KitBadge,
  KitCloseButton,
  type KitFact,
  KitFactList,
  KitPanel,
  KitSectionHead,
  KitSkeleton,
  KitSwatch,
  SessionEfficiency,
  TranscriptViewer,
  type WorkItemsState,
  compactRef,
  formatAbsolute,
  hasDelivery,
  hubApi,
} from 'fleet';
import { LensLink } from 'fleet/shell';

import { ChunkArtifacts } from '../chunk-dock/chunk-artifacts';
import type { LeaseTranscript } from './lease-transcript.query';
import { type PlacedStep, type StepRef, hostLabel, stepHeld, stepRefOf } from './step.model';
import type { EnvTimelineStepView } from './step.query';
import {
  type DrawerOrigin,
  type StepProject,
  journeyBlocks,
  localStamp,
  neighbours,
  stepFacts,
  stepIdentity,
  stepModel,
} from './step-drawer.model';

/** The transcript's placeholder: a few turns as the stacked transcript lays them out — each a stamp, a kind, and its
 * lines, ragged as prose runs. */
const TRANSCRIPT_SKELETON: readonly (readonly string[])[] = [
  ['94%', '81%', '36%'],
  ['68%'],
  ['97%', '88%', '92%', '54%'],
  ['76%', '42%'],
  ['89%', '63%'],
];

/**
 * A node step in depth: the session that ran it, where and on what, when, what it spent and produced, the chunk's whole
 * path across environments with this step marked, the chunk's reference column, and the session's transcript.
 *
 * Presentational: the step read and its neighbours' reads come in; a pick along the path and the close button go out.
 */
@Component({
  selector: 'app-step-drawer-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ChunkArtifacts,
    ChunkDelivery,
    ChunkFacts,
    ChunkIssuePane,
    KitAsyncState,
    KitBadge,
    KitCloseButton,
    KitFactList,
    KitPanel,
    KitSectionHead,
    KitSkeleton,
    KitSwatch,
    LensLink,
    RouterLink,
    SessionEfficiency,
    TranscriptViewer,
  ],
  templateUrl: './step-drawer-view.html',
  styleUrls: ['../../core/drawer/drawer-header.css', '../../core/drawer/drawer-body.css', './node-shades.css', './step-drawer-view.css'],
})
export class StepDrawerView {
  /** The step read's state. */
  readonly state = input<KitAsyncStateValue>('ready');
  /** The step read — the chunk's whole path, and the named step's own detail. */
  readonly view = input<EnvTimelineStepView | undefined>(undefined);
  /** The step shown, off the chunk's path. */
  readonly placed = input<PlacedStep | null>(null);
  /** Whether `view` is still the previous step's, while the shown step's own detail reads. */
  readonly stepPending = input(false);
  /** The hub's now — where a running step's span ends. */
  readonly nowMs = input.required<number>();
  readonly project = input<StepProject | null>(null);
  /** The step's chunk, once read. */
  readonly chunk = input<ChunkDetail | null>(null);
  readonly workItems = input<WorkItemsState>({ status: 'loading', items: [] });
  /** The chunk with only this step's artifacts. */
  readonly stepArtifacts = input<ChunkDetail | null>(null);
  /** The session's transcript; `null` when the caller may not read it. */
  readonly transcript = input<LeaseTranscript | undefined>(undefined);
  readonly transcriptState = input<KitAsyncStateValue>('loading');
  /** What a link to the chunk's page carries, so its back link returns here. */
  readonly origin = input<DrawerOrigin>({ layout: null, from: null, fromAt: null });

  /** A step picked along the chunk's path. */
  readonly selectStep = output<StepRef>();
  readonly dismiss = output<void>();

  protected readonly journey = computed(() => journeyBlocks(this.view(), this.placed(), this.nowMs()));
  protected readonly around = computed(() => neighbours(this.view(), this.placed()));
  protected readonly showDelivery = computed(() => {
    const c = this.chunk();
    return c !== null && hasDelivery(c);
  });
  /** The step's own detail, once it is the shown step's — not the previous step's held over. */
  private readonly detail = computed(() => (this.stepPending() ? undefined : this.view()));
  protected readonly escalations = computed(() => this.detail()?.escalations ?? []);
  protected readonly spend = computed(() => this.detail()?.spend ?? null);
  protected readonly model = computed(() => stepModel(this.detail()));

  private readonly beganTpl = viewChild.required<TemplateRef<unknown>>('beganTpl');
  private readonly endedTpl = viewChild.required<TemplateRef<unknown>>('endedTpl');
  private readonly spendTpl = viewChild.required<TemplateRef<unknown>>('spendTpl');
  /** The step's facts, in the board's label/value grid. */
  protected readonly stepFacts = computed<readonly KitFact[]>(() =>
    stepFacts(this.placed(), this.nowMs(), { began: this.beganTpl(), ended: this.endedTpl(), spend: this.spendTpl() }),
  );

  protected readonly transcriptSkeleton = TRANSCRIPT_SKELETON;
  /** The generated executor constant, for the template to compare a step's executor against. */
  protected readonly Executor = hubApi.Executor;
  protected readonly ref = compactRef;
  protected readonly absolute = formatAbsolute;
  protected readonly host = hostLabel;
  protected readonly stepOf = stepRefOf;
  protected readonly identity = stepIdentity;

  protected when(iso: string): string {
    return localStamp(iso, new Date(this.nowMs()));
  }

  protected held(placed: PlacedStep): string {
    return stepHeld(placed.segment, this.nowMs());
  }
}
