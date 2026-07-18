import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitPanel } from '../kit/kit-panel';
import type { RunnerRow } from './runner-panel';

/**
 * The runner registry's presentational half (issue #80) — the registry
 * table's markup, liveness dot, claim lines, pause-brake badges, and the
 * pause/resume toggle. Renders exactly the rows it is handed; injects no
 * query or mutation, so a spec drives it with plain inputs.
 */
@Component({
  selector: 'fleet-runner-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitPanel],
  template: `
    <fleet-kit-panel
      aria-label="Runner registry"
      data-testid="runner-panel"
      label="Runners · fleet registry"
      [count]="rows().length || null"
      countTestid="runners-count"
    >
      @if (rows().length === 0) {
        <p class="none" data-testid="runners-empty">NO RUNNERS REGISTERED</p>
      } @else {
        <ul class="runners" data-testid="runner-list">
            @for (row of rows(); track row.runner_id) {
              <li
                class="runner"
                data-testid="runner"
                [class.offline]="!row.online"
                [attr.data-runner]="row.runner_id"
                [attr.data-online]="row.online"
                [attr.data-hub-paused]="row.hub_paused"
                [attr.data-locally-paused]="row.locally_paused"
              >
                <div class="r1">
                  <span class="name" data-testid="runner-id">
                    <span
                      class="dot"
                      [class.online]="row.online"
                      [class.offline]="!row.online"
                      aria-hidden="true"
                    ></span>
                    {{ row.runner_id }}
                  </span>
                  <span class="seen" data-testid="runner-seen">{{ seenLabel(row) }}</span>
                </div>
                <div class="r2">
                  <span class="wid">{{ row.workspace_id }}</span>
                </div>
                <!-- The chunks this runner currently holds a route on, and the node each
                     sits at — folded in by the container from the board's own chunk
                     list, so the registry and the board can never disagree about who is
                     working what. -->
                @if (row.claims.length > 0) {
                  <ul class="claims" data-testid="runner-claims">
                    @for (claim of row.claims; track claim.chunkId) {
                      <li class="claim" data-testid="runner-claim">
                        <span class="c-id" [attr.title]="claim.chunkId">{{ claim.shortId }}</span>
                        <span class="c-node">{{ claim.node }}</span>
                      </li>
                    }
                  </ul>
                }
                <div class="r3">
                  <span class="badges">
                    <!-- Two brakes, two badges: a runner stopped by both shows both, because
                         they are cleared by different people in different places. -->
                    @if (row.locally_paused) {
                      <span
                        class="badge paused local"
                        data-testid="runner-locally-paused"
                        [title]="localPauseHint(row)"
                        >LOCALLY PAUSED</span
                      >
                    }
                    @if (row.hub_paused) {
                      <span class="badge paused hub" data-testid="runner-hub-paused">HUB PAUSED</span>
                    }
                  </span>
                  <!-- The button is the hub's brake only: the board cannot clear a brake the
                       runner set on itself, so it never offers to. -->
                  <button
                    type="button"
                    class="act"
                    data-testid="runner-toggle"
                    [attr.aria-label]="(row.hub_paused ? 'Resume ' : 'Pause ') + row.runner_id + ' at the hub'"
                    [title]="toggleHint(row)"
                    (click)="togglePause.emit(row)"
                  >
                    {{ row.hub_paused ? 'Resume' : 'Pause' }}
                  </button>
                </div>
              </li>
            }
          </ul>
        }
    </fleet-kit-panel>
  `,
  styles: `
    :host {
      display: flex;
      flex-direction: column;
      min-height: 0;
      flex: none;
      font-family: var(--mono);
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
      color: var(--text);
    }
    .none {
      color: var(--label-dim);
      padding: 10px 8px;
      margin: 0;
      font-size: var(--fs-sm);
      letter-spacing: 0.08em;
    }
    .runners {
      list-style: none;
      margin: 0;
      padding: 0;
    }
    .runner {
      display: flex;
      flex-direction: column;
      gap: 3px;
      padding: 6px 8px;
      border-bottom: 1px solid var(--line);
    }
    .r1,
    .r3 {
      display: flex;
      justify-content: space-between;
      align-items: baseline;
      gap: 6px;
    }
    /* A square, per the mockup's status indicators — not a dot. */
    .dot {
      display: inline-block;
      width: 7px;
      height: 7px;
      margin-right: 6px;
    }
    .dot.online {
      background: var(--green);
      box-shadow: 0 0 4px var(--green);
    }
    /* An offline runner blinks red: the fleet is missing a machine, which is the
       one registry fact worth pulling an operator's eye across the room. */
    .dot.offline {
      background: var(--red);
      animation: blink 1.2s steps(2, jump-none) infinite;
    }
    @keyframes blink {
      50% {
        opacity: 0.15;
      }
    }
    @media (prefers-reduced-motion: reduce) {
      .dot.offline {
        animation: none;
      }
    }
    .name {
      color: var(--amber);
      font-size: var(--fs-md);
      overflow-wrap: anywhere;
    }
    .runner.offline .name {
      color: var(--label);
    }
    .wid {
      color: var(--label);
      font-size: var(--fs-xs);
      overflow-wrap: anywhere;
    }
    .seen {
      color: var(--label-dim);
      font-size: var(--fs-label);
      white-space: nowrap;
    }
    /* The runner's claims: chunk short name — node, one line each, dim so the
       registry row's own identity stays the loudest thing in it. */
    .claims {
      list-style: none;
      margin: 0;
      padding: 0 0 0 13px;
      display: flex;
      flex-direction: column;
      gap: 1px;
    }
    .claim {
      display: flex;
      align-items: baseline;
      gap: 6px;
      font-size: var(--fs-xs);
    }
    .claim .c-id {
      color: var(--amber-hi);
    }
    .claim .c-node {
      color: var(--label);
      font-size: var(--fs-label);
      letter-spacing: 0.1em;
      text-transform: uppercase;
    }
    .badges {
      display: flex;
      flex-wrap: wrap;
      gap: 3px;
    }
    .badge.paused {
      border: 1px solid var(--line);
      font-size: var(--fs-label);
      letter-spacing: 0.12em;
      padding: 0 4px;
    }
    /* Distinct hues so "who stopped it" reads at a glance, not just from the text. */
    .badge.paused.hub {
      color: var(--amber-hi);
    }
    .badge.paused.local {
      color: var(--label-dim);
    }
    .act {
      font-family: inherit;
      background: var(--panel-deep);
      border: 1px solid var(--cyan-dim);
      color: var(--cyan);
      cursor: pointer;
      padding: 1px 6px;
      font-size: var(--fs-xs);
    }
    .act:hover {
      border-color: var(--cyan);
    }
  `,
})
export class RunnerPanelView {
  /** The registry rows to render — each runner plus its pre-folded claims. */
  readonly rows = input.required<readonly RunnerRow[]>();

  /** Emitted with the row to flip the **hub** brake on — the container reads
   * `hub_paused` off it to decide pause vs. resume. Named `togglePause`, not
   * `toggle` — `@angular-eslint/no-output-native` forbids an output shadowing
   * the native DOM `toggle` event. */
  readonly togglePause = output<RunnerRow>();

  /**
   * Why the runner stopped itself (issue #61): a spend-ceiling crossing names the ceiling
   * and the spend it reported (`locally_paused_reason`); a manual `blizzard runner pause`
   * carries none, so this falls back to the generic clear-it-yourself hint.
   */
  protected localPauseHint(row: RunnerRow): string {
    return row.locally_paused_reason ?? 'This runner paused itself. Clear it on the runner: blizzard runner start';
  }

  /** Why resuming at the hub may not start a runner: its own brake is not ours to clear. */
  protected toggleHint(row: RunnerRow): string {
    if (row.hub_paused && row.locally_paused) {
      return 'Resuming here clears the hub brake only — this runner also paused itself.';
    }
    return row.hub_paused ? 'Resume this runner at the hub' : 'Pause this runner at the hub';
  }

  /**
   * A compact "seen 12s ago" liveness label from `last_seen_at`.
   *
   * Liveness is decided where both instants share one clock — the hub, via `online`
   * (`derive_online` compares `last_seen_at` against the hub's own clock); this
   * label is decoration computed against the *browser's* clock, and a browser's clock
   * must never make a correctness call. A small negative age (`-60s <= age < 0`) is
   * benign browser-vs-hub skew — `last_seen_at` is hub-stamped and an unsynced laptop
   * is genuinely minutes off — so it reads as "just now". A larger negative age is
   * *not* skew (a wire timestamp missing its UTC offset would produce exactly this,
   * `bzh:utc-instants`), and confidently printing `0s` would mask a runner that has
   * actually been unreachable for hours — so it falls through to the hub-derived
   * `online` state instead of guessing.
   */
  protected seenLabel(row: RunnerRow): string {
    const seen = Date.parse(row.last_seen_at);
    if (Number.isNaN(seen)) return row.online ? 'online' : 'offline';
    const secondsAgo = Math.round((Date.now() - seen) / 1000);
    if (secondsAgo < -60) return row.online ? 'online' : 'offline';
    const clamped = Math.max(0, secondsAgo);
    if (clamped < 60) return `seen ${clamped}s ago`;
    const minutesAgo = Math.round(clamped / 60);
    if (minutesAgo < 60) return `seen ${minutesAgo}m ago`;
    return `seen ${Math.round(minutesAgo / 60)}h ago`;
  }
}
