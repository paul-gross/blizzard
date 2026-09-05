import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, RouterLink } from '@angular/router';
import {
  type AnswerQuestionEvent,
  ChunkGeneralTab,
  ChunkPageHeader,
  ChunkPageShell,
  ChunkTranscriptsContainer,
  deriveWorkItemsState,
  type EditGraphEvent,
  hubClient,
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
import { ChunkNodeHistoryContainer } from './chunk-node-history-container';

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
 * rather than folded in here to keep this file under `web:lint`'s line cap).
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
  templateUrl: './chunk-page.html',
  styleUrl: './chunk-page.css',
})
export class ChunkPage {
  private readonly route = inject(ActivatedRoute);

  /** The plane seam {@link ChunkTranscriptsContainer} crosses (D5, runner-node-grouped-
   * transcripts) — exposed as an instance field so the template can bind it; a plain
   * module import is not itself a template expression. */
  protected readonly hubClient = hubClient;

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
    this.selection.select(tab as ChunkDetailTab, this.selection.artifactKey());
  }

  /** A node activated in the Node history tab, or in {@link ChunkGeneralTab}'s own
   * node-history summary, writes its join key back to the URL and switches to the Node
   * history tab — both forward the same `pickStep`, a pure function of that param,
   * never their own selection state. */
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
  /** Every prerequisite this chunk still waits on, and every chunk it still holds up —
   * the whole edge set the identity line names, rather than `blocked`'s one
   * representative. A satisfied edge blocks nothing and is left off. */
  protected readonly blockedBy = computed<readonly string[]>(() =>
    (this.detail()?.neighborhood?.prerequisites ?? []).filter((n) => !n.satisfied).map((n) => n.chunk_id),
  );

  protected readonly blocking = computed<readonly string[]>(() =>
    (this.detail()?.neighborhood?.dependents ?? []).filter((n) => !n.satisfied).map((n) => n.chunk_id),
  );

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
      { decisionId: event.decisionId, choice: event.choice, chunkId: event.chunkId, struck: event.struck },
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
