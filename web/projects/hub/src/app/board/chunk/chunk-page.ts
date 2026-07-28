import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import {
  type AnswerQuestionEvent,
  ChunkAwaitingHuman,
  ChunkFacts,
  ChunkIssuePane,
  ChunkTimeline,
  ChunkTokenBreakdown,
  type EditGraphEvent,
  KitAsyncState,
  type KitAsyncStateValue,
  KitBackBar,
  KitBadge,
  KitPanel,
  type WorkItemsState,
  type ResolveDecisionEvent,
  STATUS_TONE,
  compactRef,
  errorMessage,
  injectAnswerQuestionMutation,
  injectHubChunkDetailQuery,
  injectHubChunkWorkItemsQuery,
  injectResolveDecisionMutation,
  injectSetChunkGraphMutation,
  readAnswerFailure,
} from 'fleet';

import { ArtifactLinks } from './artifact-links';

/**
 * The mobile chunk detail page (`/board/chunk/:chunkId`) — everything the
 * desktop dock shows, **stacked** in one scrolling column instead of three
 * side-by-side sections.
 *
 * A routed page reached from the glance board's rows, not a dock: the mobile
 * board is already a single column, so a detail region below it would push the
 * board off-screen. Reading a chunk is a drill-down on a phone, and a route
 * makes it deep-linkable and back-button-navigable for free.
 *
 * Every region is a `fleet` presentational sibling reused verbatim
 * (`bzh:frontend-kit`) — the same {@link ChunkFacts} + {@link ChunkTokenBreakdown},
 * {@link ChunkIssuePane}, {@link ChunkTimeline} and {@link ChunkAwaitingHuman} the
 * desktop {@link ChunkDetailPanel} composes. This page only picks the order the
 * mock's attention model wants — what the work *is* (facts, then the work-source
 * issues), then the path it took (node history), then what it is waiting on, then
 * what it produced — and wraps each in a `KitPanel` so the column reads as
 * sections rather than one undifferentiated scroll.
 *
 * **Artifacts are the one region not reused**: their bodies are long enough to
 * bury everything above them in a single column, so {@link ArtifactLinks} renders
 * an index and each row opens {@link ArtifactPage} one level deeper.
 *
 * Scope note, deliberate rather than an oversight: the dock's **destructive and
 * structural** operator actions — detach, pause/resume, close — are not mounted
 * here (they need the confirm affordances {@link ChunkDetailHeader} carries, and a
 * phone is a poor place to fire them). The **human-loop** actions are, because
 * answering an ask from a phone is the whole point of a mobile board: answer,
 * resolve, and the not-ready graph/model edits {@link ChunkFacts} exposes all
 * write through the same mutations the desktop container uses.
 */
@Component({
  selector: 'app-chunk-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ArtifactLinks,
    ChunkAwaitingHuman,
    ChunkFacts,
    ChunkIssuePane,
    ChunkTimeline,
    ChunkTokenBreakdown,
    KitAsyncState,
    KitBackBar,
    KitBadge,
    KitPanel,
    RouterLink,
  ],
  template: `
    <div class="cp">
      <a class="back-row" routerLink="/board" data-testid="mobile-chunk-back">
        <fleet-kit-back-bar label="Board" />
      </a>
      @if (actionError(); as err) {
        <p class="notice" data-testid="mobile-chunk-action-error" role="alert">{{ err }}</p>
      }
      @if (actionOutcome(); as outcome) {
        <p class="outcome" data-testid="mobile-chunk-action-outcome" role="status">{{ outcome }}</p>
      }
      @if (detail(); as d) {
        <div class="cp-sections" data-testid="board-chunk-detail">
          <header class="cp-hdr">
            <span class="cid" data-testid="mobile-chunk-ref">{{ shortId() }}</span>
            <fleet-kit-badge [tone]="tone()" variant="soft" data-testid="mobile-chunk-status">{{ d.status }}</fleet-kit-badge>
            <span class="node" data-testid="mobile-chunk-node">{{ nodeLabel() }}</span>
          </header>
          <fleet-kit-panel class="section" data-testid="section-work-item" label="work item" [count]="pointerCount() || null">
            <fleet-chunk-detail-facts [detail]="d" (editGraph)="onEditGraph($event)">
              <fleet-chunk-detail-token-breakdown token-breakdown [detail]="d" />
            </fleet-chunk-detail-facts>
          </fleet-kit-panel>
          <fleet-kit-panel class="section" data-testid="section-issues" label="issues">
            <fleet-chunk-detail-issue-pane [workItems]="workItems()" />
          </fleet-kit-panel>
          <fleet-kit-panel class="section" data-testid="section-node-history" label="node history">
            <fleet-chunk-detail-timeline [detail]="d" />
          </fleet-kit-panel>
          <fleet-kit-panel class="section" data-testid="section-asks" label="asks · decisions">
            <fleet-chunk-detail-awaiting-human
              [detail]="d"
              (answerQuestion)="onAnswer($event)"
              (resolveDecision)="onResolve($event)"
            />
          </fleet-kit-panel>
          <fleet-kit-panel class="section" data-testid="section-artifacts" label="artifacts" [count]="artifactCount() || null">
            <app-artifact-links [chunkId]="d.chunk_id" [artifacts]="d.artifacts ?? []" />
          </fleet-kit-panel>
        </div>
      } @else {
        <div class="rest">
          <fleet-kit-async-state
            [state]="state()"
            loadingText="LOADING…"
            loadingTestid="mobile-chunk-loading"
            errorText="CHUNK UNAVAILABLE"
            errorTestid="mobile-chunk-error"
          />
        </div>
      }
    </div>
  `,
  styles: `
    :host {
      display: block;
      flex: 1;
      min-height: 0;
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
      color: var(--text);
    }
    /* Height-capped with the sections owning the scroll, so the back link stays
       reachable at the top of a long chunk — the same shape the runner's own
       mobile shell gives its titlebar. */
    .cp {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      overflow: hidden;
    }
    .back-row {
      flex: none;
      text-decoration: none;
    }
    /* An operator action's failure reads directly under the back row — the
       action's own result, above the panels that fired it, never swallowed —
       the same stance fleet's desktop container takes. */
    .notice {
      flex: none;
      margin: 6px;
      padding: 4px 6px;
      border: 1px solid var(--red-dim);
      border-left-width: 2px;
      background: var(--overlay-20);
      color: var(--red);
      font-size: var(--fs-xs);
    }
    /* An outcome sits in the same slot but reads cyan, not red: losing an answer race
       is news — the question *is* answered — not something to retry. The desktop dock
       renders the same distinction the same way. */
    .outcome {
      flex: none;
      margin: 6px;
      padding: 4px 6px;
      border: 1px solid var(--line);
      border-left: 2px solid var(--cyan);
      background: var(--overlay-20);
      color: var(--text);
      font-size: var(--fs-xs);
    }
    .cp-sections {
      display: flex;
      flex-direction: column;
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      gap: 8px;
      padding: 8px;
    }
    .cp-hdr {
      display: flex;
      flex-wrap: wrap;
      align-items: baseline;
      gap: 8px;
      flex: none;
    }
    .cid {
      color: var(--amber);
      font-size: var(--fs-md);
    }
    .node {
      color: var(--label);
      font-size: var(--fs-xs);
    }
    fleet-kit-panel.section {
      flex: none;
    }
    /* Positioned and height-bearing so KitAsyncState's absolutely centered
       status line has a box to center in. */
    .rest {
      position: relative;
      flex: 1;
      min-height: 0;
    }
  `,
})
export class ChunkPage {
  private readonly route = inject(ActivatedRoute);

  /** The chunk this page is for, off the route's own `:chunkId` segment —
   * seeded from the snapshot so the first render already keys the reads. */
  private readonly params = toSignal(this.route.paramMap, { initialValue: this.route.snapshot.paramMap });

  private readonly id = computed<string | null>(() => this.params().get('chunkId'));

  private readonly detailQuery = injectHubChunkDetailQuery(() => this.id());
  private readonly workItemsQuery = injectHubChunkWorkItemsQuery(() => this.id());
  private readonly answerMutation = injectAnswerQuestionMutation();
  private readonly resolveMutation = injectResolveDecisionMutation();
  private readonly editGraphMutation = injectSetChunkGraphMutation();

  /** The chunk aggregate, or `undefined` while the first read is in flight. */
  protected readonly detail = computed(() => this.detailQuery.data());

  /** Which pre-detail state renders — a failed read is not the same as a slow one. */
  protected readonly state = computed<KitAsyncStateValue>(() =>
    this.detailQuery.isError() ? 'error' : 'loading',
  );

  /** The last operator-action failure on this chunk, or `null`. The desktop
   * container holds the same signal for the same reason (issue #42's "report,
   * don't swallow"): without it a 404/409/422 is a tap that appears to do
   * nothing, which on a phone is the only feedback there is. Cleared on the next
   * attempt. */
  protected readonly actionError = signal<string | null>(null);

  /** The last operator-action **outcome** — a non-failure result that still needs saying
   * (issue #165): today, the winning answer a lost first-write-wins race returns. This
   * page needs it at least as much as the desktop dock does — answering from a phone is
   * what it exists for, so it is the surface most likely to *lose* a race, and folding
   * that 409 through `errorMessage()` told the answerer their action failed while the
   * question was in fact answered. Cleared alongside {@link actionError}. */
  protected readonly actionOutcome = signal<string | null>(null);

  /** The chunk's related work-source items, in the shape the issue pane reads.
   * Mirrors the desktop container's own fold (`fleet`'s `chunk-detail.ts`). */
  protected readonly workItems = computed<WorkItemsState>(() => {
    if (this.workItemsQuery.isError()) return { status: 'error', items: [] };
    if (this.workItemsQuery.isPending()) return { status: 'loading', items: [] };
    return { status: 'success', items: this.workItemsQuery.data()?.items ?? [] };
  });

  protected readonly shortId = computed(() => compactRef(this.id() ?? ''));
  protected readonly tone = computed(() => STATUS_TONE[this.detail()?.status ?? 'ready']);
  protected readonly nodeLabel = computed(() => {
    const d = this.detail();
    return d?.current_node_name ?? d?.current_node_id ?? '—';
  });
  protected readonly pointerCount = computed(() => this.detail()?.work_refs?.length ?? 0);
  protected readonly artifactCount = computed(() => this.detail()?.artifacts?.length ?? 0);

  /** Clear both report channels — every action on this page starts here. Kept as one
   * method for the same reason the desktop container does: a stale outcome left up while
   * another action reports a failure reads as though the two are related. */
  private beginAction(): void {
    this.actionError.set(null);
    this.actionOutcome.set(null);
  }

  /** Answer an open question. A lost first-write-wins race returns a 409 carrying the
   * *winning* answer, which reads as an outcome naming the winner rather than a failure;
   * `readAnswerFailure` owns that fold so this page and the desktop dock cannot drift. */
  protected onAnswer(event: AnswerQuestionEvent): void {
    this.beginAction();
    this.answerMutation.mutate(
      { questionId: event.questionId, answer: event.answer, chunkId: event.chunkId },
      {
        onError: (error) => {
          const failure = readAnswerFailure(error);
          if (failure.kind === 'outcome') this.actionOutcome.set(failure.message);
          else this.actionError.set(failure.message);
        },
      },
    );
  }

  protected onResolve(event: ResolveDecisionEvent): void {
    this.beginAction();
    this.resolveMutation.mutate(
      { decisionId: event.decisionId, choice: event.choice, chunkId: event.chunkId },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Resolve failed.')) },
    );
  }

  protected onEditGraph(event: EditGraphEvent): void {
    this.beginAction();
    this.editGraphMutation.mutate(
      { chunkId: event.chunkId, graphId: event.graphId },
      { onError: (error) => this.actionError.set(errorMessage(error, 'Set graph failed.')) },
    );
  }
}
