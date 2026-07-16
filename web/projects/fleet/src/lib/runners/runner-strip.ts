import { ChangeDetectionStrategy, Component, computed } from '@angular/core';

import type { RunnerView } from '../api/hub';
import { injectHubRunnersQuery } from './runners.query';
import { injectRunnerPauseMutation } from './runners.mutations';

/**
 * The runner strip (D-019/D-043/D-070) — the fleet registry along the board's edge:
 * each registered runner with its derived **liveness** (`online` vs the staleness
 * threshold), last-seen time, and **paused** state, plus a pause/resume toggle — the
 * operator's brake, declarative state the runner reads on its outbound pull (D-012).
 *
 * A container: it owns the registry query and the pause mutation, through the
 * generated client (bzh:generated-client); the live-update service re-reads on
 * `runner-changed`.
 */
@Component({
  selector: 'fleet-runner-strip',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <section class="strip" aria-label="Runner registry" data-testid="runner-strip">
      <span class="lbl">Runners</span>
      @if (runners().length === 0) {
        <span class="none" data-testid="runners-empty">no runners registered</span>
      } @else {
        <ul class="runners" data-testid="runner-list">
          @for (runner of runners(); track runner.runner_id) {
            <li
              class="runner"
              data-testid="runner"
              [attr.data-runner]="runner.runner_id"
              [attr.data-online]="runner.online"
              [attr.data-hub-paused]="runner.hub_paused"
              [attr.data-locally-paused]="runner.locally_paused"
            >
              <span class="dot" [class.online]="runner.online" [class.offline]="!runner.online" aria-hidden="true"></span>
              <span class="rid" data-testid="runner-id">{{ runner.runner_id }}</span>
              <span class="wid">{{ runner.workspace_id }}</span>
              <span class="seen" data-testid="runner-seen">{{ seenLabel(runner) }}</span>
              <!-- Two brakes, two badges: a runner stopped by both shows both, because
                   they are cleared by different people in different places. -->
              @if (runner.locally_paused) {
                <span
                  class="badge paused local"
                  data-testid="runner-locally-paused"
                  title="This runner paused itself. Clear it on the runner: blizzard runner start"
                  >LOCALLY PAUSED</span
                >
              }
              @if (runner.hub_paused) {
                <span class="badge paused hub" data-testid="runner-hub-paused">HUB PAUSED</span>
              }
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
            </li>
          }
        </ul>
      }
    </section>
  `,
  styles: `
    :host {
      display: block;
      font-family: var(--mono);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
      color: var(--text);
    }
    .strip {
      display: flex;
      align-items: center;
      gap: 10px;
      padding: 4px 8px;
      border-top: 1px solid var(--bezel);
      background: rgba(0, 0, 0, 0.25);
      overflow-x: auto;
    }
    .lbl {
      font-size: 9px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
      flex: none;
    }
    .none {
      color: var(--label-dim);
      font-size: 10px;
    }
    .runners {
      list-style: none;
      margin: 0;
      padding: 0;
      display: flex;
      gap: 8px;
    }
    .runner {
      display: flex;
      align-items: center;
      gap: 6px;
      padding: 2px 6px;
      border: 1px solid var(--line);
      background: rgba(0, 0, 0, 0.2);
      flex: none;
    }
    .dot {
      width: 7px;
      height: 7px;
      border-radius: 50%;
      flex: none;
    }
    .dot.online {
      background: var(--cyan);
      box-shadow: 0 0 4px var(--cyan);
    }
    .dot.offline {
      background: var(--label-dim);
    }
    .rid {
      color: var(--cyan);
      font-size: 11px;
    }
    .wid {
      color: var(--label-dim);
      font-size: 10px;
    }
    .seen {
      color: var(--label-dim);
      font-size: 9px;
    }
    .badge.paused {
      border: 1px solid var(--line);
      font-size: 8px;
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
      background: rgba(0, 0, 0, 0.3);
      border: 1px solid var(--line);
      color: var(--text);
      cursor: pointer;
      padding: 1px 6px;
      font-size: 10px;
    }
    .act:hover {
      border-color: var(--cyan);
    }
  `,
})
export class RunnerStrip {
  private readonly runnersQuery = injectHubRunnersQuery();
  private readonly pauseMutation = injectRunnerPauseMutation();

  /** The fleet registry; empty until the first read resolves. */
  protected readonly runners = computed<readonly RunnerView[]>(() => this.runnersQuery.data() ?? []);

  protected toggle(runner: RunnerView): void {
    this.pauseMutation.mutate({ runnerId: runner.runner_id, paused: !runner.hub_paused });
  }

  /** Why resuming at the hub may not start a runner: its own brake is not ours to clear. */
  protected toggleHint(runner: RunnerView): string {
    if (runner.hub_paused && runner.locally_paused) {
      return 'Resuming here clears the hub brake only — this runner also paused itself.';
    }
    return runner.hub_paused ? 'Resume this runner at the hub' : 'Pause this runner at the hub';
  }

  /** A compact "seen 12s ago" liveness label from `last_seen_at`. */
  protected seenLabel(runner: RunnerView): string {
    const seen = Date.parse(runner.last_seen_at);
    if (Number.isNaN(seen)) return runner.online ? 'online' : 'offline';
    const secondsAgo = Math.max(0, Math.round((Date.now() - seen) / 1000));
    if (secondsAgo < 60) return `seen ${secondsAgo}s ago`;
    const minutesAgo = Math.round(secondsAgo / 60);
    if (minutesAgo < 60) return `seen ${minutesAgo}m ago`;
    return `seen ${Math.round(minutesAgo / 60)}h ago`;
  }
}
