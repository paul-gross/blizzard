import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { KitAsyncState, TranscriptViewer, type runnerApi } from 'fleet';

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
 * Eight read states, kept visually and testably distinct (`data-testid` per row)
 * so an operator, or a test, can never mistake one for another — each is a real
 * state a live transcript read can be in, not just the populated case:
 *
 * - **no selection** — `leaseId()` is `null`; the query is never even enabled.
 * - **loading** — the read is in flight (`isPending()`).
 * - **query error** — a genuine transport fault (network/`503`); `isError()`.
 * - **`reason: "spawning"`** — the lease exists but has no `session_id` yet
 *   (the agent hasn't started). Lease-keyed URLs make this expressible instead
 *   of collapsing into a 404.
 * - **`reason: "not_found"`** — a session id is known but no transcript file is
 *   on disk (not yet flushed, cleaned up, or a closed lease whose file rotated
 *   away). This is a **normal** state of a healthy agent, not a
 *   fault — hence `--label-dim`, never `--red`.
 * - **`reason: "unreadable"`** — the file exists but could not be parsed
 *   (permissions, corruption) — a genuine fault, `--red`.
 * - **unknown** — `available: false` with a `reason` outside the three above
 *   (or an unresolved read the earlier branches didn't already catch); the
 *   `@default` fallback the not-found case used to also catch, given its own
 *   row so the two are never mistaken for each other.
 * - **turns** — {@link TranscriptViewer}'s rendered list, plus a truncation banner
 *   when the server capped the read (truncation must be visible, never silent).
 *
 * `spawning`/`not_found` are deliberately **not** colored as errors: training an
 * operator to see red for a normal lifecycle state teaches them to ignore red.
 * Only a genuine fault (`isError()`, `unreadable`) is `--red`.
 */
@Component({
  selector: 'local-transcript-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, TranscriptViewer],
  template: `
    @if (leaseId() === null) {
      <fleet-kit-async-state state="empty" emptyText="SELECT AN AGENT" emptyTestid="transcript-empty" />
    } @else if (transcriptQuery.isPending()) {
      <fleet-kit-async-state state="loading" loadingText="LOADING TRANSCRIPT…" loadingTestid="transcript-loading" />
    } @else if (transcriptQuery.isError()) {
      <fleet-kit-async-state
        state="error"
        errorText="TRANSCRIPT UNAVAILABLE — RUNNER LOCAL API UNREACHABLE"
        errorTestid="transcript-error"
      />
    } @else if (!transcript()?.available) {
      @switch (transcript()?.reason) {
        @case ('spawning') {
          <fleet-kit-async-state
            state="empty"
            tone="accent"
            emptyText="NO TRANSCRIPT YET — AGENT STARTING"
            emptyTestid="transcript-spawning"
          />
        }
        @case ('unreadable') {
          <fleet-kit-async-state state="error" errorText="TRANSCRIPT UNREADABLE" errorTestid="transcript-unreadable" />
        }
        @case ('not_found') {
          <fleet-kit-async-state
            state="empty"
            [emptyText]="'NO TRANSCRIPT ON DISK · SESSION ' + (transcript()?.session_id ?? '—')"
            emptyTestid="transcript-not-found"
          />
        }
        @default {
          <fleet-kit-async-state state="empty" emptyText="TRANSCRIPT STATE UNKNOWN" emptyTestid="transcript-unknown" />
        }
      }
    } @else {
      <div data-testid="transcript-turns">
        @if (transcript()?.truncated) {
          <p class="banner" data-testid="transcript-truncated">TRUNCATED — SOME CONTENT WAS DROPPED</p>
        }
        <fleet-transcript-viewer [turns]="transcript()?.turns ?? []" />
      </div>
    }
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
      position: relative;
      font-family: var(--mono);
      font-size: var(--fs-base);
    }
    .banner {
      color: var(--amber-hi);
      font-size: var(--fs-sm);
      letter-spacing: 0.1em;
      padding: 5px 8px;
      border-bottom: 1px solid var(--line);
      background: var(--overlay-25);
    }
  `,
})
export class TranscriptPanel {
  /** The selected lease's id, or `null` when nothing is selected (issue #29 C1). */
  readonly leaseId = input<string | null>(null);

  protected readonly transcriptQuery = injectTranscriptQuery(this.leaseId);

  protected readonly transcript = computed<runnerApi.TranscriptResponse | undefined>(() => this.transcriptQuery.data());
}
