import { ChangeDetectionStrategy, Component, computed } from '@angular/core';

import type { RunnerView } from '../api/hub';
import { compactRef } from '../compact-ref';
import { injectHubChunksQuery } from '../chunks/chunks.query';
import { injectHubRunnersQuery } from './runners.query';
import { injectRunnerPauseMutation } from './runners.mutations';

/** One claim line under a registry row: the chunk a runner holds and where it sits. */
interface ClaimLine {
  readonly chunkId: string;
  readonly shortId: string;
  readonly node: string;
}

/**
 * The runner panel — the fleet registry in the board's right rail: each
 * registered runner with its derived **liveness** (`online` vs the
 * staleness threshold), last-seen time, and **paused** state, plus a pause/resume
 * toggle — the operator's brake, declarative state the runner reads on its
 * outbound pull.
 *
 * A container: it owns the registry query and the pause mutation, through the
 * generated client (bzh:generated-client); the live-update service re-reads on
 * `runner-changed`.
 */
@Component({
  selector: 'fleet-runner-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="panel" aria-label="Runner registry" data-testid="runner-panel">
      <div class="panel-head">
        <span class="lbl">Runners · fleet registry</span>
        <span class="lbl" data-testid="runners-count">{{ runners().length || '' }}</span>
      </div>
      <div class="panel-body">
        @if (runners().length === 0) {
          <p class="none" data-testid="runners-empty">NO RUNNERS REGISTERED</p>
        } @else {
          <ul class="runners" data-testid="runner-list">
            @for (runner of runners(); track runner.runner_id) {
              <li
                class="runner"
                data-testid="runner"
                [class.offline]="!runner.online"
                [attr.data-runner]="runner.runner_id"
                [attr.data-online]="runner.online"
                [attr.data-hub-paused]="runner.hub_paused"
                [attr.data-locally-paused]="runner.locally_paused"
              >
                <div class="r1">
                  <span class="name" data-testid="runner-id">
                    <span
                      class="dot"
                      [class.online]="runner.online"
                      [class.offline]="!runner.online"
                      aria-hidden="true"
                    ></span>
                    {{ runner.runner_id }}
                  </span>
                  <span class="seen" data-testid="runner-seen">{{ seenLabel(runner) }}</span>
                </div>
                <div class="r2">
                  <span class="wid">{{ runner.workspace_id }}</span>
                </div>
                <!-- The chunks this runner currently holds a route on, and the node each
                     sits at — read off the board's own chunk list, so the registry and
                     the board can never disagree about who is working what. -->
                @if (claimsFor(runner.runner_id); as claims) {
                  @if (claims.length > 0) {
                    <ul class="claims" data-testid="runner-claims">
                      @for (claim of claims; track claim.chunkId) {
                        <li class="claim" data-testid="runner-claim">
                          <span class="c-id" [attr.title]="claim.chunkId">{{ claim.shortId }}</span>
                          <span class="c-node">{{ claim.node }}</span>
                        </li>
                      }
                    </ul>
                  }
                }
                <div class="r3">
                  <span class="badges">
                    <!-- Two brakes, two badges: a runner stopped by both shows both, because
                         they are cleared by different people in different places. -->
                    @if (runner.locally_paused) {
                      <span
                        class="badge paused local"
                        data-testid="runner-locally-paused"
                        [title]="localPauseHint(runner)"
                        >LOCALLY PAUSED</span
                      >
                    }
                    @if (runner.hub_paused) {
                      <span class="badge paused hub" data-testid="runner-hub-paused">HUB PAUSED</span>
                    }
                  </span>
                  <!-- The button is the hub's brake only: the board cannot clear a brake the
                       runner set on itself, so it never offers to. -->
                  <button
                    type="button"
                    class="act"
                    data-testid="runner-toggle"
                    [attr.aria-label]="(runner.hub_paused ? 'Resume ' : 'Pause ') + runner.runner_id + ' at the hub'"
                    [title]="toggleHint(runner)"
                    (click)="toggle(runner)"
                  >
                    {{ runner.hub_paused ? 'Resume' : 'Pause' }}
                  </button>
                </div>
              </li>
            }
          </ul>
        }
      </div>
    </section>
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
    .panel {
      background: linear-gradient(180deg, var(--panel) 0%, var(--panel-deep) 100%);
      border: 1px solid var(--bezel);
      display: flex;
      flex-direction: column;
      min-height: 0;
    }
    .panel-head {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
      padding: 4px 8px;
      border-bottom: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.25);
      flex: none;
    }
    .lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
      text-shadow: 0 1px 0 rgba(0, 0, 0, 0.9);
    }
    .panel-body {
      overflow-y: auto;
      overflow-x: hidden;
      min-height: 0;
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
export class RunnerPanel {
  private readonly runnersQuery = injectHubRunnersQuery();
  private readonly chunksQuery = injectHubChunksQuery();
  private readonly pauseMutation = injectRunnerPauseMutation();

  /** The fleet registry; empty until the first read resolves. */
  protected readonly runners = computed<readonly RunnerView[]>(() => this.runnersQuery.data() ?? []);

  /** Every routed chunk grouped by the runner holding it — each as a claim line
   * (short name + current node) for the registry rows. */
  private readonly claims = computed<Map<string, ClaimLine[]>>(() => {
    const grouped = new Map<string, ClaimLine[]>();
    for (const chunk of this.chunksQuery.data() ?? []) {
      if (!chunk.runner_id) continue;
      const lines = grouped.get(chunk.runner_id) ?? [];
      lines.push({
        chunkId: chunk.chunk_id,
        shortId: compactRef(chunk.chunk_id),
        node: chunk.current_node_name ?? chunk.current_node_id ?? '—',
      });
      grouped.set(chunk.runner_id, lines);
    }
    return grouped;
  });

  protected claimsFor(runnerId: string): readonly ClaimLine[] {
    return this.claims().get(runnerId) ?? [];
  }

  protected toggle(runner: RunnerView): void {
    this.pauseMutation.mutate({ runnerId: runner.runner_id, paused: !runner.hub_paused });
  }

  /**
   * Why the runner stopped itself (issue #61): a spend-ceiling crossing names the ceiling
   * and the spend it reported (`locally_paused_reason`); a manual `blizzard runner pause`
   * carries none, so this falls back to the generic clear-it-yourself hint.
   */
  protected localPauseHint(runner: RunnerView): string {
    return runner.locally_paused_reason ?? 'This runner paused itself. Clear it on the runner: blizzard runner start';
  }

  /** Why resuming at the hub may not start a runner: its own brake is not ours to clear. */
  protected toggleHint(runner: RunnerView): string {
    if (runner.hub_paused && runner.locally_paused) {
      return 'Resuming here clears the hub brake only — this runner also paused itself.';
    }
    return runner.hub_paused ? 'Resume this runner at the hub' : 'Pause this runner at the hub';
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
  protected seenLabel(runner: RunnerView): string {
    const seen = Date.parse(runner.last_seen_at);
    if (Number.isNaN(seen)) return runner.online ? 'online' : 'offline';
    const secondsAgo = Math.round((Date.now() - seen) / 1000);
    if (secondsAgo < -60) return runner.online ? 'online' : 'offline';
    const clamped = Math.max(0, secondsAgo);
    if (clamped < 60) return `seen ${clamped}s ago`;
    const minutesAgo = Math.round(clamped / 60);
    if (minutesAgo < 60) return `seen ${minutesAgo}m ago`;
    return `seen ${Math.round(minutesAgo / 60)}h ago`;
  }
}
