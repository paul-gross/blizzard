import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';

import { AgentRow } from './agent-row';
import { injectRunnerLeasesQuery } from './leases.query';
import { TranscriptPanel } from './transcript-panel';

/**
 * The runner's machine-local panel — the runner app's own view, added on top of
 * the shared fleet views. Its critical path is `GET /api/leases` (issue
 * #28): every active lease, each rendered as a {@link AgentRow}. Hub-free by
 * design — the machine panel is precisely the part
 * of the app that must not depend on the hub — the query polls the runner's
 * own local API on a 5s floor, no SSE. The local surface (held environments,
 * open asks, escalations — `blizzard runner status`) lands on
 * this shell as those features arrive. Color comes from the shared
 * design-token layer (`fleet` library, design/tokens.css), never hard-coded hex.
 *
 * Three read states, kept visually distinct so a `503` (the store unwired, or
 * the runner not yet hosting a workspace) can never be mistaken for the
 * genuinely-idle empty state:
 * - `isPending()` — the first read hasn't resolved yet;
 * - `isError()` — the read failed and there is no cached data to fall back on;
 * - resolved with an empty list — no active leases, the loop is idle.
 *
 * Two-pane shell: the lease list is the left pane; {@link selectedLeaseId}
 * tracks which row (if any) is current, fed by each {@link AgentRow}'s
 * `(selectLease)` output and reflected back onto every row via `[selected]` so
 * exactly one row is ever marked current. The right pane is
 * {@link TranscriptPanel} (issue #29 slice C) — it owns the transcript read and
 * every empty/loading/error state on its own; this shell only ever passes it
 * {@link selectedLeaseId} and never branches on the read itself.
 *
 * **Closed rows** (issue #29 slice C): `GET /api/leases` now returns
 * active leases followed by recently-closed ones, in that server-decided order
 * (`LocalLeaseService.list_recent()` — one owner of the ordering). This list
 * renders them as the *same* {@link AgentRow}, not a second pane or a grouped
 * session browser — {@link firstClosedIndex} finds where the closed block
 * starts (purely to draw one subtle divider) but never reorders or filters
 * anything the server already decided.
 */
@Component({
  selector: 'fleet-local-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [AgentRow, TranscriptPanel],
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
        <div class="lease-pane" data-testid="lease-pane">
          @if (leasesQuery.isPending()) {
            <p class="status" data-testid="loading-state">LOADING…</p>
          } @else if (leasesQuery.isError()) {
            <p class="status error" data-testid="error-state">LEASES UNAVAILABLE — RUNNER LOCAL API UNREACHABLE</p>
          } @else if (leases().length === 0) {
            <p class="status empty" data-testid="empty-state">NO ACTIVITY — RUNNER IDLE</p>
          } @else {
            <div class="rows" data-testid="lease-rows">
              @for (lease of leases(); track lease.lease_id; let i = $index) {
                @if (i === firstClosedIndex() && firstClosedIndex() > 0) {
                  <div class="divider" data-testid="closed-divider"></div>
                }
                <fleet-agent-row
                  [agent]="lease"
                  [selected]="lease.lease_id === selectedLeaseId()"
                  (selectLease)="selectedLeaseId.set($event)"
                />
              }
            </div>
          }
        </div>
        <div class="transcript-pane" data-testid="transcript-pane">
          <fleet-transcript-panel [leaseId]="selectedLeaseId()" />
        </div>
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
      font-size: var(--fs-base);
      font-variant-numeric: tabular-nums;
    }
    .lp {
      display: flex;
      flex-direction: column;
      height: 100%;
    }
    .lbl {
      font-size: var(--fs-label);
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
      background: linear-gradient(180deg, var(--header-hi), var(--header-lo));
    }
    .brand {
      display: flex;
      flex-direction: column;
      justify-content: center;
      padding: 0 14px;
      border-right: 1px solid var(--line);
      color: var(--amber-hi);
      font-size: var(--fs-lg);
      letter-spacing: 0.28em;
      text-transform: uppercase;
    }
    .brand small {
      color: var(--label);
      font-size: var(--fs-label);
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
      font-size: var(--fs-lg);
    }
    .body {
      flex: 1;
      min-height: 0;
      display: flex;
      flex-direction: row;
      overflow: hidden;
    }
    .lease-pane {
      flex: 0 0 340px;
      min-width: 0;
      position: relative;
      overflow-y: auto;
      border-right: 1px solid var(--bezel);
    }
    .transcript-pane {
      flex: 1;
      min-width: 0;
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
      font-size: var(--fs-sm);
      letter-spacing: 0.12em;
    }
    .status.error {
      color: var(--red);
    }
    .rows {
      display: flex;
      flex-direction: column;
    }
    .divider {
      height: 1px;
      background: var(--bezel-hi);
      margin: 4px 0;
    }
  `,
})
export class LocalPanel {
  /** A short connection/health status shown in the header (e.g. `ok`, `offline`). */
  readonly connection = input('—');

  protected readonly leasesQuery = injectRunnerLeasesQuery();

  /** The active + recently-closed leases, server-ordered; empty until the first read resolves. */
  protected readonly leases = computed(() => this.leasesQuery.data() ?? []);

  /**
   * The index of the first `closed` row in {@link leases} (`-1` when there is
   * none), used only to draw one subtle divider between the active block and
   * the closed block (issue #29 slice C) — never to reorder or filter,
   * both of which the server (`list_recent()`) already did.
   */
  protected readonly firstClosedIndex = computed(() => this.leases().findIndex((lease) => lease.state === 'closed'));

  /**
   * The `lease_id` of the row currently marked selected, or `null` when no
   * row has been picked yet. Fed by every {@link AgentRow}'s `(selectLease)`
   * output; reflected back onto each row via `[selected]`, and passed straight
   * through to {@link TranscriptPanel} (issue #29).
   */
  protected readonly selectedLeaseId = signal<string | null>(null);
}
