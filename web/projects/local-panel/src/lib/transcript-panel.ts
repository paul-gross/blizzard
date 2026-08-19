import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { KitAsyncState, mergeLateLinks, TranscriptViewer, type runnerApi } from 'fleet';

import { injectTranscriptQuery } from './transcript.query';

/**
 * The right pane's content (issue #29 slice C) — one lease's parsed transcript, driven
 * by {@link injectTranscriptQuery}. Standalone, `OnPush`, self-contained: `local-panel.ts`
 * only ever passes it {@link leaseId} and never branches on the read itself — every
 * degraded/empty case below is this component's own concern. Turn rendering itself is
 * `fleet`'s shared {@link TranscriptViewer} (blizzard#248 D3/D4) — this component is now
 * only the container: it owns the query and maps it onto one of the states below, the
 * pattern the hub's Transcripts tab (blizzard#248 Phase 2) reuses rather than reinvents.
 *
 * Nine read states, kept visually and testably distinct (`data-testid` per row)
 * so an operator, or a test, can never mistake one for another — each is a real
 * state a live transcript read can be in, not just the populated case:
 *
 * - **no selection** — `leaseId()` is `null`; the query is never even enabled.
 * - **loading** — the read is in flight (`isPending()`).
 * - **query error** — a genuine transport fault (network/`503`); `isError()`.
 * - **`reason: "spawning"`** — the lease exists but has no `session_id` yet
 *   (the agent hasn't started). Lease-keyed URLs make this expressible instead
 *   of collapsing into a 404.
 * - **hub-unreachable** (`hub_unreachable: true`, blizzard#249 D1) — a closed
 *   lease whose hub could not be asked *and* whose local file cannot answer
 *   either. Checked ahead of the `reason` switch below, whatever `reason` the
 *   failed local read carried, because "the hub could not be asked" must never
 *   read as the routine `not_found` case (visible-degrade precedent:
 *   `local-info.ts`, not `chunk-title.query.ts`). **Not** the same as a closed
 *   lease whose hub is unreachable but whose *local* file still answers — D1
 *   folds that case into a quiet local fallback (the service leaves
 *   `hub_unreachable` `false`), so it renders as plain turns below, same as
 *   any other local read.
 * - **`reason: "not_found"`** — a session id is known but no transcript file is
 *   on disk (not yet flushed, cleaned up, or a closed lease whose file rotated
 *   away and the hub holds nothing either). This is a **normal** state of a
 *   healthy agent, not a fault — hence `--label-dim`, never `--red`.
 * - **`reason: "unreadable"`** — the file exists but could not be parsed
 *   (permissions, corruption) — a genuine fault, `--red`.
 * - **unknown** — `available: false` with a `reason` outside the three above
 *   (or an unresolved read the earlier branches didn't already catch); the
 *   `@default` fallback the not-found case used to also catch, given its own
 *   row so the two are never mistaken for each other.
 * - **turns** — {@link TranscriptViewer}'s rendered list, plus two banners that layer
 *   onto it when they apply: a truncation banner when the server capped the read
 *   (truncation must be visible, never silent), and an archived badge
 *   (`transcript-archived-badge`) when `provenance: "archived"` — the turns came from
 *   the hub's archive rather than the live local file (blizzard#249 D1).
 *
 * `spawning`/`not_found` are deliberately **not** colored as errors: training an
 * operator to see red for a normal lifecycle state teaches them to ignore red.
 * A genuine fault (`isError()`, `unreadable`) is `--red` — and so is
 * hub-unreachable, closer in spirit to a "we don't know" fault than to a
 * normal lifecycle step.
 */
@Component({
  selector: 'local-transcript-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, TranscriptViewer],
  templateUrl: './transcript-panel.html',
  styleUrl: './transcript-panel.css',
})
export class TranscriptPanel {
  /** The selected lease's id, or `null` when nothing is selected (issue #29 C1). */
  readonly leaseId = input<string | null>(null);

  protected readonly transcriptQuery = injectTranscriptQuery(this.leaseId);

  protected readonly transcript = computed<runnerApi.TranscriptResponse | undefined>(() => this.transcriptQuery.data());

  /** This panel reads a transcript WHOLE (cold), so it has no late links of its own to fold —
   * but a closed lease's transcript is served from the hub (blizzard#249), which does. Applied
   * unconditionally: a no-op on the local read, correct on the resolved one. */
  protected readonly mergedTurns = computed(() => mergeLateLinks(this.transcript()?.turns ?? []));
}
