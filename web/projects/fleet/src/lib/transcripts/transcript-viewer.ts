import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { formatAbsolute, formatLocalClockWithDay, type LocalClockWithDay } from '../when';
import type { TranscriptSidechain, TranscriptTool, TranscriptTurn } from './transcript-turn';

/** {@link TranscriptViewer.inputPreview}'s own cap (`review:F8`) — the `<summary>` line
 * clamps visually via CSS (`.tc-input`'s `text-overflow: ellipsis`), but a tool call's
 * structured input can run to megabytes, and `inputPreview` runs on every change-detection
 * pass; capping the string itself keeps that `JSON.stringify` output — and the DOM node
 * holding it — bounded regardless of how the container is styled. */
const MAX_INPUT_PREVIEW_CHARS = 300;

/**
 * The shared, presentational turn list (blizzard#248 D3/D4) — one component both the
 * runner's local panel (`local-panel/src/lib/transcript-panel.ts`) and the hub's chunk
 * Transcripts tab render, over the structural {@link TranscriptTurn} shape. Injects
 * nothing and owns no query; a container passes `turns` and this component only ever
 * renders what it is given (`bzh:frontend-container-presentational`).
 *
 * Four turn kinds render distinctly:
 * - `env`/`asst` — plain text, as `local-transcript-panel` always rendered them.
 * - `tool` — a `<details>` card naming the call, its structured input, and its output
 *   (or a running placeholder while `tool.output` is still `null`); a tool call that
 *   spawned a sidechain nests this same component recursively inside the card, behind
 *   its own open-standalone control (blizzard#248 D7 — "nested and standalone
 *   sidechains are one component", read both nested-in-context and standalone;
 *   `review:F3`).
 * - `thinking` — collapsed by default, expanding in place; a redacted turn
 *   (`thinking_redacted`) shows a presence placeholder instead of prose.
 * - `sidechain` — an *unlinked* sidechain (blizzard#248 D7: no spawning tool call to
 *   nest under) renders as its own top-level entry, recursing the same way a nested one
 *   does.
 *
 * `openStandalone` always emits the turn that owns the sidechain (a `tool` turn for a
 * nested one, a `sidechain` turn for an unlinked one) and is forwarded through every
 * recursive instantiation, so a bubbled emission from a nested viewer reaches the
 * container that turns it into a URL-held selection instead of being swallowed partway
 * up (`review:F3`).
 *
 * `turnClockInfo`/`turnAbsolute` render a turn's `timestamp` in the viewer's own local
 * zone (issue #136, `bzh:utc-instants`) — unchanged from `local-transcript-panel`'s own
 * copy, which this component now owns instead.
 */
@Component({
  selector: 'fleet-transcript-viewer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [],
  template: `
    <div class="turns">
      @for (turn of turns(); track turn.index) {
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
                    <span class="tc-name">{{ turn.tool?.name }}</span>
                    <span class="tc-input">{{ inputPreview(turn.tool) }}</span>
                  </summary>
                  <div class="tc-out">{{ turn.tool?.output ?? 'running…' }}</div>
                  @if (turn.sidechain; as sidechain) {
                    <div class="sidechain" data-testid="transcript-sidechain-nested">
                      <button
                        type="button"
                        class="sc-head sc-open"
                        data-testid="transcript-sidechain-open"
                        (click)="openStandalone.emit(turn)"
                      >
                        {{ sidechainLabel(sidechain) }} · open standalone
                      </button>
                      <fleet-transcript-viewer [turns]="sidechain.turns" (openStandalone)="openStandalone.emit($event)" />
                    </div>
                  }
                </details>
              }
              @case ('thinking') {
                <details class="thinking">
                  <summary class="th-head">thinking</summary>
                  @if (turn.thinking_redacted) {
                    <div class="th-redacted">redacted</div>
                  } @else {
                    <div class="th-body">{{ turn.text }}</div>
                  }
                </details>
              }
              @case ('sidechain') {
                @if (turn.sidechain; as sidechain) {
                  <div class="sidechain" data-testid="transcript-sidechain-standalone">
                    <button
                      type="button"
                      class="sc-head sc-open"
                      data-testid="transcript-sidechain-open"
                      (click)="openStandalone.emit(turn)"
                    >
                      {{ sidechainLabel(sidechain) }} · open standalone
                    </button>
                    <fleet-transcript-viewer [turns]="sidechain.turns" (openStandalone)="openStandalone.emit($event)" />
                  </div>
                }
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
  `,
  styles: `
    :host {
      display: block;
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
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
    .turn.k-thinking .g .tick,
    .turn.k-sidechain .g .tick {
      background: var(--amber-dim, var(--label-dim));
      border-color: var(--amber, var(--label-dim));
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
      display: flex;
      align-items: baseline;
      gap: 6px;
      min-width: 0;
      cursor: pointer;
      list-style: none;
    }
    .tool-call .tc-head::-webkit-details-marker {
      display: none;
    }
    .tool-call .tc-name {
      flex: none;
      color: var(--green);
      font-size: var(--fs-sm);
    }
    .tool-call .tc-input {
      flex: 1;
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
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
    .thinking {
      border: 1px solid var(--line);
      border-left: 2px solid var(--label-dim);
      background: var(--overlay-30);
      padding: 2px 6px;
    }
    .thinking .th-head {
      cursor: pointer;
      list-style: none;
      color: var(--label-dim);
      font-size: var(--fs-label);
      letter-spacing: 0.12em;
      text-transform: uppercase;
    }
    .thinking .th-head::-webkit-details-marker {
      display: none;
    }
    .thinking .th-body {
      margin-top: 3px;
      color: var(--label);
      white-space: pre-wrap;
      font-size: var(--fs-sm);
    }
    .thinking .th-redacted {
      margin-top: 3px;
      color: var(--label-dim);
      font-style: italic;
    }
    .sidechain {
      margin-top: 6px;
      padding-left: 8px;
      border-left: 2px dashed var(--line);
    }
    .sidechain .sc-head {
      color: var(--label-dim);
      font-size: var(--fs-label);
      letter-spacing: 0.1em;
      text-transform: uppercase;
      margin-bottom: 2px;
    }
    .sidechain .sc-open {
      display: block;
      background: transparent;
      border: none;
      padding: 0;
      font-family: inherit;
      cursor: pointer;
      text-align: left;
    }
    .sidechain .sc-open:hover {
      color: var(--cyan);
    }
  `,
})
export class TranscriptViewer {
  /** The turns to render, in order — this component never fetches or filters them. */
  readonly turns = input.required<readonly TranscriptTurn[]>();

  /** Emitted with the turn that owns a sidechain — nested (`tool`) or unlinked
   * (`sidechain`) — when the operator asks to view it standalone (blizzard#248 D7,
   * `review:F3`). This component always renders the sidechain inline too; a container
   * with no standalone concept (the runner's local panel) needs no listener at all; the
   * hub's Transcripts tab is the one that turns this into a URL-held selection. */
  readonly openStandalone = output<TranscriptTurn>();

  protected inputPreview(tool: TranscriptTool | null): string {
    if (tool === null) return '';
    const raw = tool.input_shape === 'object' ? JSON.stringify(tool.input) : (tool.input_unparsed ?? '');
    return raw.length > MAX_INPUT_PREVIEW_CHARS ? `${raw.slice(0, MAX_INPUT_PREVIEW_CHARS)}…` : raw;
  }

  protected sidechainLabel(sidechain: TranscriptSidechain): string {
    const who = sidechain.agent_type ?? sidechain.agent_id ?? 'subagent';
    return sidechain.link === 'unlinked' ? `${who} · unlinked` : who;
  }

  /**
   * A turn's browser-local `HH:MM:SS` plus day context, or `null` when
   * absent/unparsable — the template's time cell falls back to `—`. Moved from
   * `local-transcript-panel` unchanged (issue #136, `bzh:utc-instants`): the wire and
   * the runner store stay UTC end to end, this is display decoration over that instant.
   */
  protected turnClockInfo(iso: string | null): LocalClockWithDay | null {
    return formatLocalClockWithDay(iso);
  }

  /** {@link turnClockInfo}'s full local date + time, for the stamp's hover tooltip. */
  protected turnAbsolute(iso: string | null): string {
    return formatAbsolute(iso);
  }
}
