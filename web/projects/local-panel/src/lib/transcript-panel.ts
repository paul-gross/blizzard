import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { formatUtcClock, KitAsyncState, type runnerApi } from 'fleet';

import { injectTranscriptQuery } from './transcript.query';

/**
 * `02:41:36 UTC` from an ISO-8601 instant, or `—` when absent/unparsable.
 *
 * This is the panel's only *absolute* time-of-day rendering (`agent-row.ts`
 * deliberately renders *relative* ages instead, to sidestep clock questions
 * entirely — `bzh:utc-instants`). Rendered in UTC rather than the viewer's
 * local zone and labeled as such: an operator can be anywhere, but the wire is
 * UTC end to end, so a fixed, explicitly-labeled zone reads the same turn the
 * same way regardless of who is looking, instead of silently matching
 * whichever browser happens to be open. The `HH:MM:SS` parse/slice itself is
 * `fleet`'s shared {@link formatUtcClock} (issue #81); this wrapper owns only
 * the panel's own `UTC`-suffixed / `—` display shape.
 */
function formatTurnTimestamp(iso: string | null): string {
  const clock = formatUtcClock(iso);
  return clock ? `${clock} UTC` : '—';
}

/**
 * The right pane's content (issue #29 slice C) — one lease's parsed transcript,
 * turn by turn, driven by {@link injectTranscriptQuery}. Standalone, `OnPush`,
 * self-contained: `local-panel.ts` only ever passes it {@link leaseId} and never
 * branches on the read itself — every degraded/empty case below is this
 * component's own concern.
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
 * - **turns** — the parsed list, plus a truncation banner when the server
 *   capped the read (truncation must be visible, never silent).
 *
 * `spawning`/`not_found` are deliberately **not** colored as errors: training an
 * operator to see red for a normal lifecycle state teaches them to ignore red.
 * Only a genuine fault (`isError()`, `unreadable`) is `--red`.
 */
@Component({
  selector: 'local-transcript-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState],
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
      <div class="turns" data-testid="transcript-turns">
        @if (transcript()?.truncated) {
          <p class="banner" data-testid="transcript-truncated">TRUNCATED — SHOWING THE MOST RECENT TURNS</p>
        }
        @for (turn of transcript()?.turns ?? []; track turn.index) {
          <div class="turn" [class]="'k-' + turn.kind" data-testid="transcript-turn">
            <span class="t">{{ formatTurnTimestamp(turn.timestamp) }}</span>
            <span class="g"><span class="tick"></span></span>
            <span class="b">
              @switch (turn.kind) {
                @case ('tool') {
                  <details class="tool-call">
                    <summary class="tc-head">
                      <span class="tc-name">{{ turn.tool_name }} <b>{{ turn.tool_input }}</b></span>
                    </summary>
                    <div class="tc-out">{{ turn.tool_output ?? 'running…' }}</div>
                  </details>
                }
                @case ('env') {
                  <div class="who">env</div>
                  <div class="tx">{{ turn.text }}</div>
                }
                @default {
                  <div class="who">assistant</div>
                  <div class="tx">{{ turn.text }}</div>
                }
              }
              @if (turn.truncated) {
                <div class="trunc-note">⋯ truncated</div>
              }
            </span>
          </div>
        }
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
      font-variant-numeric: tabular-nums;
    }
    .banner {
      color: var(--amber-hi);
      font-size: var(--fs-sm);
      letter-spacing: 0.1em;
      padding: 5px 8px;
      border-bottom: 1px solid var(--line);
      background: var(--overlay-25);
    }
    .turns {
      padding: 4px 0 12px;
    }
    .turn {
      display: grid;
      grid-template-columns: 56px 16px 1fr;
      gap: 8px;
      padding: 4px 10px 4px 8px;
      border-bottom: 1px solid var(--line);
    }
    .turn .t {
      color: var(--label-dim);
      font-size: var(--fs-label);
      padding-top: 2px;
    }
    .turn .g {
      position: relative;
    }
    .turn .g::before {
      content: '';
      position: absolute;
      left: 6px;
      top: 0;
      bottom: -1px;
      width: 1px;
      background: var(--line);
    }
    .turn .g .tick {
      position: absolute;
      left: 3px;
      top: 5px;
      width: 7px;
      height: 7px;
      background: var(--panel-deep);
      border: 1px solid var(--label-dim);
      z-index: 1;
    }
    .turn.k-tool .g .tick {
      background: var(--green-dim);
      border-color: var(--green);
    }
    .turn.k-env .g .tick {
      background: var(--cyan-dim);
      border-color: var(--cyan);
    }
    .turn .b {
      min-width: 0;
      font-size: var(--fs-sm);
      line-height: 1.55;
    }
    .turn .who {
      font-size: var(--fs-label);
      letter-spacing: 0.16em;
      text-transform: uppercase;
      color: var(--label);
      margin-bottom: 1px;
    }
    .turn.k-env .who {
      color: var(--cyan);
    }
    .turn .tx {
      color: var(--text);
      white-space: pre-wrap;
    }
    .turn.k-env .tx {
      color: var(--label);
      font-size: var(--fs-sm);
    }
    .trunc-note {
      color: var(--amber-dim);
      font-size: var(--fs-label);
      margin-top: 2px;
    }
    .tool-call {
      border: 1px solid var(--line);
      border-left: 2px solid var(--green-dim);
      background: var(--overlay-30);
      padding: 2px 6px;
    }
    .tool-call .tc-head {
      cursor: pointer;
      list-style: none;
    }
    .tool-call .tc-head::-webkit-details-marker {
      display: none;
    }
    .tool-call .tc-name {
      color: var(--green);
      font-size: var(--fs-sm);
    }
    .tool-call .tc-name b {
      color: var(--amber);
      font-weight: normal;
    }
    .tool-call .tc-out {
      margin-top: 3px;
      padding: 3px 6px;
      background: #000;
      border: 1px solid var(--line);
      color: var(--label);
      font-size: var(--fs-xs);
      white-space: pre-wrap;
      max-height: 200px;
      overflow-y: auto;
    }
  `,
})
export class TranscriptPanel {
  /** The selected lease's id, or `null` when nothing is selected (issue #29 C1). */
  readonly leaseId = input<string | null>(null);

  protected readonly transcriptQuery = injectTranscriptQuery(this.leaseId);

  protected readonly transcript = computed<runnerApi.TranscriptResponse | undefined>(() => this.transcriptQuery.data());

  protected readonly formatTurnTimestamp = formatTurnTimestamp;
}
