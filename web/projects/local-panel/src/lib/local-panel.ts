import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { AgentRow } from './agent-row';
import { injectRunnerLeasesQuery } from './leases.query';

/**
 * The runner's machine-local panel — the runner app's own view, added on top of
 * the shared fleet views (D-097). Its critical path is `GET /api/leases` (issue
 * #28): every active lease, each rendered as a {@link AgentRow}. Hub-free by
 * design (`design/runner/web-app.md`: "the machine panel is precisely the part
 * of the app that must not depend on the hub") — the query polls the runner's
 * own local API on a 5s floor, no SSE. The local surface (held environments,
 * open asks, escalations — design/cli.md `blizzard runner status`) lands on
 * this shell as those features arrive. Color comes from the shared
 * design-token layer (`fleet` library, design/tokens.css), never hard-coded hex.
 *
 * Three read states, kept visually distinct so a `503` (the store unwired, or
 * the runner not yet hosting a workspace) can never be mistaken for the
 * genuinely-idle empty state:
 * - `isPending()` — the first read hasn't resolved yet;
 * - `isError()` — the read failed and there is no cached data to fall back on;
 * - resolved with an empty list — no active leases, the loop is idle.
 */
@Component({
  selector: 'fleet-local-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [AgentRow],
  template: `
    <div class="lp" data-testid="local-panel">
      <header class="lp-header">
        <div class="brand">
          blizzard<small>runner · local panel</small>
        </div>
        <div class="spacer"></div>
        <div class="conn" data-testid="conn">
          <span class="lbl">Runner</span>
          <span class="v">{{ connection() }}</span>
        </div>
      </header>
      <section class="body">
        @if (leasesQuery.isPending()) {
          <p class="status" data-testid="loading-state">LOADING…</p>
        } @else if (leasesQuery.isError()) {
          <p class="status error" data-testid="error-state">LEASES UNAVAILABLE — RUNNER LOCAL API UNREACHABLE</p>
        } @else if (leases().length === 0) {
          <p class="status empty" data-testid="empty-state">NO ACTIVITY — RUNNER IDLE</p>
        } @else {
          <div class="rows" data-testid="lease-rows">
            @for (lease of leases(); track lease.lease_id) {
              <fleet-agent-row [agent]="lease" />
            }
          </div>
        }
      </section>
    </div>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
      background: var(--bg);
      color: var(--text);
      font-family: var(--mono);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
    }
    .lp {
      display: flex;
      flex-direction: column;
      height: 100%;
    }
    .lbl {
      font-size: 9px;
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .lp-header {
      flex: none;
      display: flex;
      align-items: stretch;
      height: 40px;
      border-bottom: 1px solid var(--bezel);
      background: linear-gradient(180deg, #0d1526, #080d18);
    }
    .brand {
      display: flex;
      flex-direction: column;
      justify-content: center;
      padding: 0 14px;
      border-right: 1px solid var(--line);
      color: var(--amber-hi);
      font-size: 15px;
      letter-spacing: 0.28em;
      text-transform: uppercase;
    }
    .brand small {
      color: var(--label);
      font-size: 9px;
      letter-spacing: 0.18em;
    }
    .spacer {
      flex: 1;
      border-right: 1px solid var(--line);
    }
    .conn {
      display: flex;
      flex-direction: column;
      justify-content: center;
      padding: 0 14px;
    }
    .conn .v {
      color: var(--cyan);
      font-size: 15px;
    }
    .body {
      flex: 1;
      min-height: 0;
      position: relative;
      overflow-y: auto;
    }
    .status {
      position: absolute;
      left: 50%;
      top: 50%;
      transform: translate(-50%, -50%);
      white-space: nowrap;
      color: var(--label-dim);
      font-size: 11px;
      letter-spacing: 0.12em;
    }
    .status.error {
      color: var(--red);
    }
    .rows {
      display: flex;
      flex-direction: column;
    }
  `,
})
export class LocalPanel {
  /** A short connection/health status shown in the header (e.g. `ok`, `offline`). */
  readonly connection = input('—');

  protected readonly leasesQuery = injectRunnerLeasesQuery();

  /** The active leases; empty until the first read resolves. */
  protected readonly leases = computed(() => this.leasesQuery.data() ?? []);
}
