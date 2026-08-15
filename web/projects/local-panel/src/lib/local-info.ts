import { ChangeDetectionStrategy, Component, computed, effect, signal } from '@angular/core';
import { ageMs, formatAge, injectNowSignal, KitAsyncState, type KitAsyncStateValue, type runnerApi } from 'fleet';

import { injectRunnerDashboardQuery } from './status.query';

/**
 * The hub-link panel — the discovery mock's "hub · outbound only, nothing
 * dials in": the configured hub endpoint, derived reachability, last flush
 * (last successful PULL contact), the outbound buffer depth, and this runner's
 * own capacities/pause state. All off `GET /api/dashboard`'s `runner` section
 * — the runner's *own* facts about its hub link, not a live hub read; the
 * board link is the one hand-off to the hub app, minted from the endpoint the
 * wire now carries.
 *
 * Below the link facts is the discovery mock's fleet counts strip
 * (ready/running/waiting/needs) — a fleet-level pulse. Those counts *are* a
 * hub read, so unlike the rest of this panel they arrive through the same
 * dashboard read's `fleet_summary` section — the runner's own `GET
 * /api/fleet-summary` pass-through (issue #76), folded in by Phase 1 (issue
 * #311): the hub API allows no cross-origin browser read, so the runner
 * forwards it. `fleet_summary` is `null` exactly when that forward fails (hub
 * unreachable or unwired) — a **200** carrying a null slot, not a failed
 * request, so the strip's degraded/last-known state can no longer be read off
 * the query's own `isError()`. See {@link latchedFleet}.
 */
@Component({
  selector: 'local-info',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState],
  template: `
    <div class="wrap" data-testid="local-info">
      <fleet-kit-async-state [state]="triadState()" loadingText="LOADING…" errorText="RUNNER STATUS UNAVAILABLE">
        @if (view(); as v) {
          <dl class="kv">
            <dt>endpoint</dt>
            <dd class="path" data-testid="hub-endpoint">{{ v.hub.endpoint }}</dd>
            <dt>link</dt>
            <dd>
              <span class="link" [class.up]="v.hub.reachable" [class.down]="!v.hub.reachable" data-testid="hub-link">
                {{ v.hub.reachable ? 'CONNECTED' : 'UNREACHABLE' }}
              </span>
            </dd>
            <dt>last flush</dt>
            <dd data-testid="hub-last-flush">{{ lastFlushLabel() }}</dd>
            <dt>buffered</dt>
            <dd data-testid="hub-buffered">{{ v.hub.buffer_depth }} events</dd>
            <dt>agents</dt>
            <dd>{{ v.capacities.used }}/{{ v.capacities.max_agents }} slots</dd>
            <dt>loop</dt>
            <dd>
              <span [class.paused]="v.pause.effective">{{ v.pause.effective ? 'PAUSED' : 'FILLING' }}</span>
              <small class="tick">· tick {{ lastTickLabel() }}</small>
            </dd>
          </dl>
          <div class="fleet-strip" [class.stale]="fleetStale()" data-testid="fleet-strip">
            <div class="fs-head">
              <span class="fs-lbl">Fleet</span>
              <span class="fs-age" data-testid="fleet-age">{{ fleetStale() ? 'last known' : 'live' }}</span>
            </div>
            <div class="fleet-nums">
              <span class="fn ready" data-testid="fleet-ready">{{ fleet()?.ready ?? '—' }}<small>ready</small></span>
              <span class="fn running" data-testid="fleet-running">{{ fleet()?.running ?? '—' }}<small>running</small></span>
              <span class="fn waiting" data-testid="fleet-waiting">{{ fleet()?.waiting ?? '—' }}<small>waiting</small></span>
              <span class="fn needs" data-testid="fleet-needs">{{ fleet()?.needs ?? '—' }}<small>needs</small></span>
            </div>
          </div>
          <a class="board-link" [href]="v.hub.endpoint" target="_blank" rel="noopener" data-testid="board-link">
            open fleet board — hub serving →
          </a>
        }
      </fleet-kit-async-state>
    </div>
  `,
  styles: `
    :host {
      display: block;
      font-family: var(--mono);
      font-variant-numeric: tabular-nums;
    }
    .wrap {
      position: relative;
      min-height: 60px;
      padding: 6px 8px;
    }
    .kv {
      display: grid;
      grid-template-columns: 88px 1fr;
      gap: 2px 10px;
      margin: 0;
      font-size: var(--fs-sm);
    }
    .kv dt {
      color: var(--label);
      text-transform: uppercase;
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
    }
    .kv dd {
      margin: 0;
      color: var(--text);
      min-width: 0;
    }
    .kv dd.path {
      color: var(--cyan);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .link.up {
      color: var(--green);
    }
    .link.down {
      color: var(--red);
    }
    .paused {
      color: var(--amber-hi);
    }
    .tick {
      color: var(--label-dim);
      font-size: var(--fs-label);
      margin-left: 6px;
    }
    .board-link {
      display: block;
      margin-top: 8px;
      color: var(--cyan);
      font-size: var(--fs-xs);
      text-decoration: none;
      letter-spacing: 0.08em;
    }
    .board-link:hover {
      text-decoration: underline;
    }
    .fleet-strip {
      position: relative;
      margin-top: 8px;
      padding: 6px 8px;
      border: 1px solid var(--bezel);
      background: rgba(0, 0, 0, 0.25);
    }
    .fleet-strip .fs-head {
      display: flex;
      justify-content: space-between;
      margin-bottom: 5px;
    }
    .fs-lbl,
    .fs-age {
      color: var(--label);
      text-transform: uppercase;
      font-size: var(--fs-label);
      letter-spacing: 0.1em;
    }
    .fleet-nums {
      display: flex;
      gap: 14px;
    }
    .fleet-nums .fn {
      font-size: var(--fs-sm);
    }
    .fleet-nums .fn small {
      display: block;
      color: var(--label);
      text-transform: uppercase;
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
    }
    .fleet-nums .fn.ready {
      color: var(--cyan);
    }
    .fleet-nums .fn.running {
      color: var(--amber);
    }
    .fleet-nums .fn.waiting {
      color: var(--amber-hi);
    }
    .fleet-nums .fn.needs {
      color: var(--red);
    }
    /* Hub unreachable: dim the strip and banner it as last-known — the mock's
       degraded state. The rest of the panel is hub-free, so it stays lit. */
    .fleet-strip.stale {
      opacity: 0.45;
    }
    .fleet-strip.stale::after {
      content: 'HUB UNREACHABLE — LAST KNOWN · LOCAL CONTROLS UNAFFECTED';
      position: absolute;
      inset: auto 0 -1px 0;
      padding: 2px 6px;
      background: var(--red-dim);
      color: var(--red-wash-text);
      font-size: var(--fs-label);
      letter-spacing: 0.08em;
    }
  `,
})
export class LocalInfo {
  protected readonly query = injectRunnerDashboardQuery();

  protected readonly view = computed(() => {
    const data = this.query.data()?.runner;
    // A malformed body (e.g. `{}` from a misrouted proxy) must render the
    // degraded state, not throw on `hub.endpoint` mid-template.
    return data?.hub && data.capacities && data.pause ? data : null;
  });

  /**
   * The fleet strip's own latch (issue #311): under the composed read, a hub
   * outage is a successful `/api/dashboard` read carrying `fleet_summary: null`
   * — TanStack sees no error and retains nothing special about the prior
   * counts, so without this the strip would blank instead of degrading. This
   * `effect()` tracks the *last non-null* `fleet_summary` seen, leaving the
   * signal untouched on a `null` read — the same "keep showing last-known"
   * behavior the old two-query shape got for free from TanStack's own error-path
   * data retention.
   */
  private readonly latchedFleet = signal<runnerApi.FleetSummaryView | null>(null);
  private readonly latchFleetSummary = effect(() => {
    const summary = this.query.data()?.fleet_summary;
    if (summary != null) this.latchedFleet.set(summary);
  });

  /** The last-known fleet counts, or `null` before the first successful read —
   * {@link latchedFleet}'s own latch, not TanStack's `data()`. */
  protected readonly fleet = computed(() => this.latchedFleet());

  /** The current read's `fleet_summary` slot is `null` (hub unreachable / not
   * wired, or no read has resolved yet) — the strip degrades to its dimmed
   * last-known state. The rest of the panel is hub-free, so it is unaffected. */
  protected readonly fleetStale = computed<boolean>(() => this.query.data()?.fleet_summary == null);

  /** The async triad's resolved state — no `'empty'` case: a resolved read
   * with a malformed body renders nothing (the `view()` null-guard in the
   * projected content), the same degraded-blank behavior as before. */
  protected readonly triadState = computed<KitAsyncStateValue>(() => {
    if (this.query.isPending()) return 'loading';
    if (this.query.isError()) return 'error';
    return 'ready';
  });

  /** Ticks once a second (issue #178) so `lastFlushLabel`/`lastTickLabel` advance
   * between polls instead of sitting frozen at whatever age the last read carried —
   * both are elapsed-time-derived with no covering event (D7), so `status.query.ts`'s
   * backstop is what refreshes their anchors, and either can read up to one backstop
   * interval staler than the daemon's true state, never fresher. */
  private readonly now = injectNowSignal(1000);

  /** `-34s` since the last successful PULL, or `never` before first contact. */
  protected readonly lastFlushLabel = computed<string>(() => {
    const contactAt = this.view()?.hub.last_contact_at ?? null;
    if (contactAt === null) return 'never';
    const age = ageMs(contactAt, this.now());
    return age === null ? '—' : formatAge(age);
  });

  protected readonly lastTickLabel = computed<string>(() => {
    const tickAt = this.view()?.last_tick_at ?? null;
    if (tickAt === null) return '—';
    const age = ageMs(tickAt, this.now());
    return age === null ? '—' : formatAge(age);
  });
}
