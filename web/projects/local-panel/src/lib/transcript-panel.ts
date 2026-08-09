import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { formatAbsolute, formatLocalClockWithDay, KitAsyncState, type LocalClockWithDay, type runnerApi } from 'fleet';

import { injectTranscriptQuery } from './transcript.query';

/**
 * The right pane's content (issue #29 slice C) — one lease's parsed transcript,
 * turn by turn, driven by {@link injectTranscriptQuery}. Standalone, `OnPush`,
 * self-contained: `local-panel.ts` only ever passes it {@link leaseId} and never
 * branches on the read itself — every degraded/empty case below is this
 * component's own concern.
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
 *   read as the routine `not_found` case — follows `local-info.ts`'s
 *   visible-degrade precedent (a dimmed last-known view plus a banner), not
 *   `chunk-title.query.ts`'s silent-degrade one. **Not** the same as a closed
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
 * - **turns** — the parsed list. Three decorations layer onto this state, each
 *   visible rather than silent when it applies: a truncation banner
 *   (`transcript-truncated`) when the server capped the read; an archived
 *   badge (`transcript-archived-badge`) when `provenance: "archived"` — the
 *   turns were served from the hub's archive, not the live local file
 *   (blizzard#249 D1); and a dropped-turns count (`transcript-dropped-turns`)
 *   when the hub→panel projection (D5) dropped turns the wire model has no
 *   slot for — zero renders nothing, so this stays a no-op for every local
 *   read, where the count is always zero.
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
      @if (transcript()?.hub_unreachable) {
        <p class="degrade-banner" data-testid="transcript-hub-unreachable">
          HUB UNREACHABLE — CANNOT READ ARCHIVED TRANSCRIPT
        </p>
      } @else {
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
      }
    } @else {
      <div class="turns" data-testid="transcript-turns">
        @if (transcript()?.provenance === 'archived') {
          <p class="banner archived" data-testid="transcript-archived-badge">ARCHIVED — SERVED FROM HUB</p>
        }
        @if (transcript()?.truncated) {
          <p class="banner" data-testid="transcript-truncated">TRUNCATED — SHOWING THE MOST RECENT TURNS</p>
        }
        @if ((transcript()?.dropped_turns ?? 0) > 0) {
          <p class="banner dropped" data-testid="transcript-dropped-turns">
            {{ transcript()?.dropped_turns }} TURNS NOT SHOWN — PANEL VIEW LACKS FULL DETAIL
          </p>
        }
        @for (turn of transcript()?.turns ?? []; track turn.index) {
          <div class="turn" [class]="'k-' + turn.kind" data-testid="transcript-turn">
            <span class="t" [attr.title]="turnClockInfo(turn.timestamp) ? turnAbsolute(turn.timestamp) : null">
              @if (turnClockInfo(turn.timestamp); as info) {
                @if (info.day) {
                  <span class="day">{{ info.day }}</span>
                }
                <span class="time">{{ info.time }}</span>
              } @else {
                <span class="time">—</span>
              }
            </span>
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
    /* Informational, not a warning — the turns are real, just sourced from the
       hub's archive instead of the live local file (blizzard#249 D1). */
    .banner.archived {
      color: var(--cyan);
    }
    /* A loss condition like the plain .banner truncation case, so it keeps the
       same amber family rather than the archived badge's informational cyan. */
    .banner.dropped {
      color: var(--amber-dim);
    }
    /* The hub-unreachable state (blizzard#249 D1) — local-info.ts's visible-degrade
       banner precedent (.fleet-strip.stale::after), not KitAsyncState's default
       centered/nowrap status line: this message is long enough that nowrap would
       push it past a narrow phone's viewport rather than wrapping. */
    .degrade-banner {
      margin: 0;
      padding: 8px 10px;
      background: var(--red-dim);
      color: #ffd9dd;
      font-size: var(--fs-sm);
      letter-spacing: 0.08em;
    }
    .turns {
      padding: 4px 0 12px;
    }
    .turn {
      display: grid;
      grid-template-columns: 64px 16px 1fr;
      gap: 8px;
      padding: 4px 10px 4px 8px;
      border-bottom: 1px solid var(--line);
    }
    .turn .t {
      display: flex;
      flex-direction: column;
      color: var(--label-dim);
      font-size: var(--fs-label);
      padding-top: 2px;
    }
    .turn .t .day {
      font-size: 0.9em;
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

  /**
   * A turn's browser-local `HH:MM:SS` plus day context, or `null` when
   * absent/unparsable — the template's time cell falls back to `—`.
   *
   * This is the panel's only *absolute* time-of-day rendering (`agent-row.ts`
   * deliberately renders *relative* ages instead, to sidestep clock questions
   * entirely — `bzh:utc-instants`). Rendered in the viewer's own local zone
   * rather than a fixed UTC label (issue #136): the wire and the runner store
   * stay UTC end to end (`bzh:utc-instants`), but an operator reading a
   * transcript wants the clock on their own wall, not the server's — this is
   * display decoration over that UTC instant, not a reinterpretation of it.
   * The parse/day-boundary logic itself is `fleet`'s shared
   * {@link formatLocalClockWithDay} (issue #136).
   */
  protected turnClockInfo(iso: string | null): LocalClockWithDay | null {
    return formatLocalClockWithDay(iso);
  }

  /** {@link turnClockInfo}'s full local date + time, for the stamp's hover tooltip
   * (issue #175). */
  protected turnAbsolute(iso: string | null): string {
    return formatAbsolute(iso);
  }
}
