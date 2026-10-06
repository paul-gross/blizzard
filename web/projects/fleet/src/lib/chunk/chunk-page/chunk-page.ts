import { ChangeDetectionStrategy, Component, ElementRef, afterRenderEffect, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';

import { ChunkArtifactsPanel } from '../chunk-artifacts-panel/chunk-artifacts-panel';
import type { AnswerQuestionEvent, ResolveDecisionEvent } from '../chunk-detail/chunk-awaiting-human';
import type { EditGraphEvent } from '../chunk-detail/chunk-facts';
import { deriveWorkItemsState, type WorkItemsState } from '../chunk-detail/work-items-state';
import { STATUS_TONE } from '../../core/chunk-lanes';
import { KitAsyncState, type KitAsyncStateValue } from '../../kit/kit-async-state';
import { KitBackBar } from '../../kit/kit-back-bar';
import { KitTabs, type KitTabOption } from '../../kit/kit-tabs';
import { ChunkTranscriptsContainer } from '../../transcripts/chunk-transcripts-container';
import { ViewportService } from '../../core/viewport/viewport-service';
import { injectChunkDetailQuery, injectChunkWorkItemsQuery } from './chunk-detail.query';
import { type ChunkDetailTab, injectChunkDetailSelection } from './chunk-detail-selection';
import { ChunkGeneralTab } from './chunk-general-tab';
import { ChunkNodeHistoryContainer } from './chunk-node-history-container';
import { CHUNK_PAGE_DAEMON } from './chunk-page-daemon';
import { chunkTabOptions, openEdgeIds, preDetailState } from './chunk-page.model';
import { ChunkPageHeader } from './chunk-page-header';
import { ChunkPageShell } from './chunk-page-shell';

/**
 * The chunk detail page (`/board/chunk/:chunkId`), written once for both daemons — the
 * hub's board and a runner's panel each mount it on their own route, handing it their
 * daemon through {@link CHUNK_PAGE_DAEMON}: the generated `client` and cache-key `plane`
 * every read on the page crosses, and an optional operator-action port. One shell serves
 * both widths; at phone width the tab bodies opt into the list/detail drill-down.
 *
 * Four tabs, selected through {@link injectChunkDetailSelection} (`?tab`, so
 * the choice is a URL-held state of this one page, not a different page):
 * **General** ({@link ChunkGeneralTab}), **Node history** ({@link ChunkNodeHistoryContainer}),
 * **Artifacts**, and **Transcripts**, the last hidden from the strip when the port says
 * the identity may not read transcripts ({@link canReadTranscripts}). A route makes any of
 * the four deep-linkable and back-button-navigable for free.
 *
 * This container keeps the back bar, the port's action-error/outcome channels, the
 * identity header, the tab strip, and the two reads every tab shares; each tab's own
 * layout is its own presentational component's job. The Node history and Transcripts
 * tabs' own queries stay off this container entirely — {@link ChunkNodeHistoryContainer}
 * and {@link ChunkTranscriptsContainer} each own theirs, mounted only inside their own
 * `@switch` branch, which is what keeps them lazy.
 *
 * Every daemon difference rides the token: with no actions port the page renders
 * read-only — no answer, resolve, or graph-edit controls, no graph links — and still
 * offers the Transcripts tab, since a daemon without a port has no permission model to
 * consult. The dock's destructive and structural operator actions are never mounted
 * here, port or not.
 */
@Component({
  selector: 'fleet-chunk-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ChunkArtifactsPanel,
    ChunkGeneralTab,
    ChunkNodeHistoryContainer,
    ChunkPageHeader,
    ChunkPageShell,
    ChunkTranscriptsContainer,
    KitAsyncState,
    KitBackBar,
    KitTabs,
    RouterLink,
  ],
  templateUrl: './chunk-page.html',
  styleUrl: './chunk-page.css',
})
export class ChunkPage {
  private readonly route = inject(ActivatedRoute);
  private readonly viewport = inject(ViewportService);
  private readonly element = inject(ElementRef<HTMLElement>);

  /** The daemon this page reads from — its `client`/`plane` are exposed to the
   * template for {@link ChunkTranscriptsContainer}'s own seam. */
  protected readonly daemon = inject(CHUNK_PAGE_DAEMON);

  /** The daemon's operator-action port for this visit, or `null` — read-only. */
  protected readonly actions = this.daemon.actions?.() ?? null;
  /** The row to restore after its mobile-only detail view removes the focused
   * control. This is focus continuity, not selection state: the URL remains the
   * sole owner of the selected node, artifact, or transcript segment. */
  private focusRequest:
    | { kind: 'detail'; testid: string }
    | { kind: 'list'; attribute: string; value: string }
    | { kind: 'tab' }
    | null = null;
  private stepOrigin: string | null = null;
  private artifactOrigin: string | null = null;
  private transcriptOrigin: string | null = null;

  protected readonly mobile = computed(() => this.viewport.mode() === 'mobile');

  constructor() {
    afterRenderEffect(() => {
      // The URL inputs make this rerun only after the selected branch has actually
      // entered or left the DOM. A deep link makes no request, so it never steals
      // initial focus.
      this.selection.stepKey();
      this.selection.artifactKey();
      this.selection.transcriptSegment();
      this.selection.transcriptSidechain();
      const request = this.focusRequest;
      if (request === null) return;

      const root = this.element.nativeElement as HTMLElement;
      const target = request.kind === 'detail'
        ? root.querySelector<HTMLElement>(`[data-testid="${request.testid}"]`)
        : request.kind === 'list'
          ? Array.from(root.querySelectorAll<HTMLElement>(`[${request.attribute}]`)).find(
              (row) => row.getAttribute(request.attribute) === request.value,
            ) ?? root.querySelector<HTMLElement>(`[data-testid="tab-${this.tab()}"]`)
          : root.querySelector<HTMLElement>(`[data-testid="tab-${this.tab()}"]`);
      target?.focus();
      this.focusRequest = null;
    });
  }

  /** The chunk this page is for, off the route's own `:chunkId` segment —
   * seeded from the snapshot so the first render already keys the reads. */
  private readonly params = toSignal(this.route.paramMap, { initialValue: this.route.snapshot.paramMap });

  /** Off the route's own `:chunkId` segment — the `?chunk` param the back link
   * writes is a different, board-owned selection (`injectChunkUrlSelection`, in
   * board-page.ts), never read from here. */
  protected readonly chunkId = computed<string | null>(() => this.params().get('chunkId'));

  protected readonly selection = injectChunkDetailSelection();

  protected readonly tab = this.selection.tab;

  protected onChooseTab(tab: string): void {
    this.selection.select(tab as ChunkDetailTab);
  }

  /** A node activated in the Node history tab, or in {@link ChunkGeneralTab}'s own
   * node-history summary, writes its join key back to the URL and switches to the Node
   * history tab — both forward the same `pickStep`, a pure function of that param,
   * never their own selection state. */
  protected onSelectStep(stepKey: string | null): void {
    if (this.mobile()) {
      if (stepKey === null) {
        this.focusRequest = this.stepOrigin === null
          ? { kind: 'tab' }
          : { kind: 'list', attribute: 'data-step-key', value: this.stepOrigin };
      } else {
        this.stepOrigin = stepKey;
        this.focusRequest = { kind: 'detail', testid: 'node-history-back' };
      }
    }
    this.selection.selectStep(stepKey);
  }

  /** A nav row picked in the Artifacts tab writes its key back to the URL —
   * {@link ChunkArtifactsPanel}'s viewer is a pure function of that param, never
   * its own selection state. */
  protected onSelectArtifact(key: string | null): void {
    if (this.mobile()) {
      if (key === null) {
        this.focusRequest = this.artifactOrigin === null
          ? { kind: 'tab' }
          : { kind: 'list', attribute: 'data-artifact-key', value: this.artifactOrigin };
      } else {
        this.artifactOrigin = key;
        this.focusRequest = { kind: 'detail', testid: 'artifacts-tab-back' };
      }
    }
    this.selection.selectArtifact(key);
  }

  /** A segment picked in the Transcripts tab writes its id back to the URL, which stays
   * the selection's only home. */
  protected onSelectTranscriptSegment(segmentId: string | null): void {
    if (this.mobile()) {
      if (segmentId === null) {
        this.focusRequest = this.transcriptOrigin === null
          ? { kind: 'tab' }
          : { kind: 'list', attribute: 'data-segment-id', value: this.transcriptOrigin };
      } else {
        this.transcriptOrigin = segmentId;
        this.focusRequest = { kind: 'detail', testid: 'transcript-segment-back' };
      }
    }
    this.selection.selectTranscriptSegment(segmentId);
  }

  /** A sidechain opened standalone in the Transcripts tab — nested under a tool call or
   * unlinked — writes its encoded `SidechainPath` back to the URL, so it is
   * deep-linkable. */
  protected onSelectTranscriptSidechain(path: string | null): void {
    if (this.mobile()) {
      this.focusRequest = {
        kind: 'detail',
        testid: path === null ? 'transcript-segment-back' : 'transcript-sidechain-back',
      };
    }
    this.selection.selectTranscriptSidechain(path);
  }

  private readonly detailQuery = injectChunkDetailQuery(
    () => this.daemon.client,
    () => this.daemon.plane,
    () => this.chunkId(),
  );

  private readonly workItemsQuery = injectChunkWorkItemsQuery(
    () => this.daemon.client,
    () => this.daemon.plane,
    () => this.chunkId(),
  );

  protected readonly canControl = computed(() => this.actions?.canControl() ?? false);
  protected readonly canAnswer = computed(() => this.actions?.canAnswer() ?? false);
  protected readonly canResolve = computed(() => this.actions?.canResolve() ?? false);
  protected readonly resolvePending = computed(() => this.actions?.resolvePending() ?? false);
  protected readonly pendingAnswerQuestionIds = computed(() => this.actions?.pendingAnswerQuestionIds() ?? []);
  protected readonly actionError = computed(() => this.actions?.actionError() ?? null);
  protected readonly actionOutcome = computed(() => this.actions?.actionOutcome() ?? null);

  /** The daemon's graphs view a graph badge links to, or `null` — unlinked. */
  protected readonly graphLinkBase = this.actions?.graphLinkBase ?? null;

  /** The daemon's events view the General tab links this chunk's events to, or `null` —
   * no link. */
  protected readonly eventsLinkBase = this.actions?.eventsLinkBase ?? null;

  /** Whether the Transcripts tab's option shows in the strip. With a port, the port's
   * permission decides — a display filter only, not the access gate. With none, always — a daemon without a
   * permission model serves its own transcripts to whoever reaches its panel. */
  protected readonly canReadTranscripts = computed(() => this.actions?.canReadTranscripts() ?? true);

  protected readonly tabOptions = computed<readonly KitTabOption[]>(() => chunkTabOptions(this.canReadTranscripts()));

  /** The chunk aggregate, or `undefined` while the first read is in flight. */
  protected readonly detail = computed(() => this.detailQuery.data());

  /** Which pre-detail state renders — a failed read is not the same as a slow one. */
  protected readonly state = computed<KitAsyncStateValue>(() => preDetailState(this.detailQuery.isError()));

  /** The chunk's related work-source items, in the shape the issue pane reads. */
  protected readonly workItems = computed<WorkItemsState>(() => deriveWorkItemsState(this.workItemsQuery));

  protected readonly tone = computed(() => STATUS_TONE[this.detail()?.status ?? 'ready']);
  /** Every prerequisite this chunk still waits on, and every chunk it still holds up —
   * the whole edge set the identity line names, rather than `blocked`'s one
   * representative. A satisfied edge blocks nothing and is left off. */
  protected readonly blockedBy = computed<readonly string[]>(() =>
    openEdgeIds(this.detail()?.neighborhood?.prerequisites ?? []),
  );

  protected readonly blocking = computed<readonly string[]>(() => openEdgeIds(this.detail()?.neighborhood?.dependents ?? []));

  protected onAnswer(event: AnswerQuestionEvent): void {
    this.actions?.answer(event);
  }

  protected onResolve(event: ResolveDecisionEvent): void {
    this.actions?.resolve(event);
  }

  protected onEditGraph(event: EditGraphEvent): void {
    this.actions?.editGraph(event);
  }
}
