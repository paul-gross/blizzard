import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import {
  type AnswerQuestionEvent,
  ChunkPageHeader,
  ChunkPageShell,
  deriveWorkItemsState,
  type EditGraphEvent,
  KitAsyncState,
  type KitAsyncStateValue,
  KitBackBar,
  KitTabs,
  type KitTabOption,
  type WorkItemsState,
  type ResolveDecisionEvent,
  STATUS_TONE,
  errorMessage,
  hasPermission,
  injectAnswerQuestionMutation,
  injectHubChunkDetailQuery,
  injectHubChunkWorkItemsQuery,
  injectMeQuery,
  injectResolveDecisionMutation,
  injectSetChunkGraphMutation,
  readAnswerFailure,
} from 'fleet';

import { ChunkArtifactsTab } from './chunk-artifacts-tab';
import { type ChunkDetailTab, injectChunkDetailSelection } from './chunk-detail-selection';
import { ChunkGeneralTab } from './chunk-general-tab';
import { ChunkNodeHistoryContainer } from './chunk-node-history-container';
import { ChunkTranscriptsContainer } from './chunk-transcripts-container';

/**
 * The chunk detail page (`/board/chunk/:chunkId`, issue #160) — reached from
 * both the mobile board's rows and the desktop dock's artifact links, on
 * desktop as well as mobile. One shell serves both widths: `app.routes.ts`
 * forks the mobile/desktop board shell in the route table, and only there —
 * so this page stays a single component tree, with the narrow case handled
 * entirely in the tab bodies' own CSS rather than a second viewport-scoped
 * page.
 *
 * Four tabs, selected through {@link injectChunkDetailSelection} (`?tab`, so
 * the choice is a URL-held state of this one page, not a different page):
 * **General** — {@link ChunkGeneralTab}, everything this page showed before it
 * grew more tabs — **Node history** ({@link ChunkNodeHistoryContainer}), **Artifacts**,
 * and **Transcripts** (blizzard#248 Phase 2), the last hidden from the strip
 * without `transcript:read` ({@link canReadTranscripts}). A route makes any of
 * the four deep-linkable and back-button-navigable for free.
 *
 * This container keeps the back bar, the shared action-error/outcome
 * channels, the identity header, the tab strip, and the queries and three
 * operator mutations every tab shares; each tab's own layout is its own
 * presentational component's job. The Node history and Transcripts tabs' own
 * queries stay off this container entirely — {@link ChunkNodeHistoryContainer} and
 * {@link ChunkTranscriptsContainer} each own theirs, mounted only inside their own
 * `@switch` branch below, which is what keeps them lazy (`review:F1`; split out
 * rather than folded in here to keep this file under `web:structural-gate`'s line cap).
 *
 * Scope note, deliberate rather than an oversight: the dock's **destructive
 * and structural** operator actions — detach, pause/resume, close — are not
 * mounted here (they need the confirm affordances {@link ChunkDetailHeader}
 * carries, and a phone is a poor place to fire them). The **human-loop**
 * actions are, because answering an ask from here is the whole point of a
 * mobile board: answer, resolve, and the not-ready graph edit
 * {@link ChunkFacts} exposes all write through the same mutations the desktop
 * container uses.
 */
const BASE_TAB_OPTIONS: readonly KitTabOption[] = [
  { value: 'general', label: 'General', testid: 'tab-general' },
  { value: 'node-history', label: 'Node history', testid: 'tab-node-history' },
  { value: 'artifacts', label: 'Artifacts', testid: 'tab-artifacts' },
];

const TRANSCRIPTS_TAB_OPTION: KitTabOption = { value: 'transcripts', label: 'Transcripts', testid: 'tab-transcripts' };

@Component({
  selector: 'app-chunk-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    ChunkArtifactsTab,
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
  template: `
    <fleet-chunk-page-shell>
      <a chunk-page-back class="back-row" routerLink="/board" [queryParams]="{ chunk: chunkId() }" data-testid="mobile-chunk-back">
        <fleet-kit-back-bar label="Board" />
      </a>
      @if (actionError(); as err) {
        <p chunk-page-notice class="notice" data-testid="mobile-chunk-action-error" role="alert">{{ err }}</p>
      }
      @if (actionOutcome(); as outcome) {
        <p chunk-page-notice class="outcome" data-testid="mobile-chunk-action-outcome" role="status">{{ outcome }}</p>
      }
      <!-- Each of the shell's named slots below gets its own single-root @if
           rather than sharing one — an @if whose root has more than one
           projectable node can leave slot-selector matching unresolved
           (NG8011); a solo root per block is unambiguous. -->
      @if (detail(); as d) {
        <fleet-chunk-page-header
          chunk-page-header
          data-testid="board-chunk-detail"
          [chunkId]="chunkId() ?? ''"
          [status]="d.status"
          [tone]="tone()"
        />
      }
      @if (detail()) {
        <fleet-kit-tabs chunk-page-tabs [options]="tabOptions()" [activeValue]="tab()" (choose)="onChooseTab($event)" />
      }
      @if (detail(); as d) {
        @switch (tab()) {
          @case ('general') {
            <app-chunk-general-tab
              [detail]="d"
              [workItems]="workItems()"
              [canControl]="canControl()"
              [canAnswer]="canAnswer()"
              [canResolve]="canResolve()"
              (answerQuestion)="onAnswer($event)"
              (resolveDecision)="onResolve($event)"
              (editGraph)="onEditGraph($event)"
            />
          }
          @case ('node-history') {
            <app-chunk-node-history-container
              [chunkId]="chunkId()"
              [detail]="d"
              [selectedKey]="selection.stepKey()"
              (pickStep)="onSelectStep($event)"
            />
          }
          @case ('artifacts') {
            <app-chunk-artifacts-tab
              [artifacts]="d.artifacts ?? []"
              [selectedKey]="selection.artifactKey()"
              (pickArtifact)="onSelectArtifact($event)"
            />
          }
          @case ('transcripts') {
            <app-chunk-transcripts-container
              [chunkId]="chunkId()"
              [history]="d.history ?? []"
              [currentNodeId]="d.current_node_id"
              [currentNodeName]="d.current_node_name ?? null"
              [latestEpoch]="d.latest_epoch"
              [segmentId]="selection.transcriptSegment()"
              [sidechainPath]="selection.transcriptSidechain()"
              (pickSegment)="onSelectTranscriptSegment($event)"
              (pickSidechain)="onSelectTranscriptSidechain($event)"
            />
          }
        }
      } @else {
        <fleet-kit-async-state
          [state]="state()"
          loadingText="LOADING…"
          loadingTestid="mobile-chunk-loading"
          errorText="CHUNK UNAVAILABLE"
          errorTestid="mobile-chunk-error"
        />
      }
    </fleet-chunk-page-shell>
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
    /* The back link's own reset: \`fleet-chunk-page-shell\` cannot style content
       projected into its slots (\`chunk-page-shell.ts\`'s own doc comment), so
       this stays page-owned — flex:none so the row keeps its content height
       as a direct flex item of the shell's own column, text-decoration:none
       to clear the anchor's UA-default underline. */
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
    app-chunk-general-tab {
      flex: 1;
      min-height: 0;
    }
    app-chunk-artifacts-tab {
      flex: 1;
      min-height: 0;
    }
    /* No rule targets \`app-chunk-node-history-container\` itself, for the same reason
       none targets \`app-chunk-transcripts-container\` below — it is \`display: contents\`;
       its presentational child carries its own \`flex: 1; min-height: 0\` host. */
    /* No rule targets \`app-chunk-transcripts-container\` itself (review:F1) — it is
       \`display: contents\`, so it generates no box of its own to size; its child
       \`app-chunk-transcripts-tab\` is a direct flex item of the shell's own
       \`.cps-body\` instead, and carries \`flex: 1; min-height: 0\` on its own
       \`:host\` (\`chunk-transcripts-tab.ts\`). */
  `,
})
export class ChunkPage {
  private readonly route = inject(ActivatedRoute);

  /** The chunk this page is for, off the route's own `:chunkId` segment —
   * seeded from the snapshot so the first render already keys the reads. */
  private readonly params = toSignal(this.route.paramMap, { initialValue: this.route.snapshot.paramMap });

  /** Off the route's own `:chunkId` segment — the `?chunk` param the back link
   * writes is a different, board-owned selection ({@link BoardSelection}),
   * never read from here. */
  protected readonly chunkId = computed<string | null>(() => this.params().get('chunkId'));

  protected readonly selection = injectChunkDetailSelection();

  protected readonly tab = this.selection.tab;

  protected onChooseTab(tab: string): void {
    this.selection.select(tab as ChunkDetailTab, this.selection.artifactKey());
  }

  /** A row activated in the Node history tab writes its join key back to the URL —
   * {@link ChunkNodeHistoryTab} forwards it straight from {@link ChunkTimeline}, a pure
   * function of that param, never its own selection state. */
  protected onSelectStep(stepKey: string | null): void {
    this.selection.selectStep(stepKey);
  }

  /** A nav row picked in the Artifacts tab writes its key back to the URL —
   * {@link ChunkArtifactsTab}'s viewer is a pure function of that param, never
   * its own selection state. */
  protected onSelectArtifact(key: string): void {
    this.selection.select('artifacts', key);
  }

  /** A segment picked in the Transcripts tab writes its id back to the URL —
   * {@link ChunkTranscriptsContainer} forwards it straight to the presentational tab,
   * a pure function of that param, never its own selection state (blizzard#248 D8). */
  protected onSelectTranscriptSegment(segmentId: string | null): void {
    this.selection.selectTranscriptSegment(segmentId);
  }

  /** A sidechain opened standalone in the Transcripts tab — nested under a tool call or
   * unlinked — writes its encoded `SidechainPath` back to the URL, so it is
   * deep-linkable (blizzard#248 D7, `review:F4`). */
  protected onSelectTranscriptSidechain(path: string | null): void {
    this.selection.selectTranscriptSidechain(path);
  }

  private readonly detailQuery = injectHubChunkDetailQuery(() => this.chunkId());
  private readonly workItemsQuery = injectHubChunkWorkItemsQuery(() => this.chunkId());
  private readonly answerMutation = injectAnswerQuestionMutation();
  private readonly resolveMutation = injectResolveDecisionMutation();
  private readonly editGraphMutation = injectSetChunkGraphMutation();
  private readonly meQuery = injectMeQuery();

  /** Whether the current identity may set the chunk's graph (`chunk:control` —
   * issue #210). `null`/pending resolves to `false` (hidden until confirmed). */
  protected readonly canControl = computed(() => hasPermission(this.meQuery.data(), 'chunk:control'));

  /** Whether the current identity may answer an open question (`question:answer`). */
  protected readonly canAnswer = computed(() => hasPermission(this.meQuery.data(), 'question:answer'));

  /** Whether the current identity may resolve an open gate decision (`gate:resolve`). */
  protected readonly canResolve = computed(() => hasPermission(this.meQuery.data(), 'gate:resolve'));

  /** Whether the current identity may read a chunk's stored transcript segments
   * (`transcript:read`, blizzard#248 D9) — the Transcripts tab's own *option* is
   * hidden from the strip without it. A deep link still reaches
   * {@link ChunkTranscriptsContainer}, which renders the backend's 403 as its own state
   * rather than relying on this client-side check to be the only gate. */
  protected readonly canReadTranscripts = computed(() => hasPermission(this.meQuery.data(), 'transcript:read'));

  protected readonly tabOptions = computed<readonly KitTabOption[]>(() =>
    this.canReadTranscripts() ? [...BASE_TAB_OPTIONS, TRANSCRIPTS_TAB_OPTION] : BASE_TAB_OPTIONS,
  );

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
  protected readonly workItems = computed<WorkItemsState>(() => deriveWorkItemsState(this.workItemsQuery));

  protected readonly tone = computed(() => STATUS_TONE[this.detail()?.status ?? 'ready']);

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
