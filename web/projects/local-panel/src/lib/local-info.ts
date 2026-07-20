import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { ageMs, formatAge, KitAsyncState, type KitAsyncStateValue } from 'fleet';

import { injectRunnerFleetSummaryQuery, injectRunnerStatusQuery } from './status.query';

/**
 * The hub-link panel — the discovery mock's "hub · outbound only, nothing
 * dials in": the configured hub endpoint, derived reachability, last flush
 * (last successful PULL contact), the outbound buffer depth, and this runner's
 * own capacities/pause state. All off `GET /api/runner` — the runner's *own*
 * facts about its hub link, not a live hub read; the board link is the one
 * hand-off to the hub app, minted from the endpoint the wire now carries.
 *
 * Below the link facts is the discovery mock's fleet counts strip
 * (ready/running/waiting/needs) — a fleet-level pulse. Those counts *are* a
 * hub read, so unlike the rest of this panel they arrive through the runner's
 * own `GET /api/fleet-summary` pass-through (issue #76): the hub API allows no
 * cross-origin browser read, so the runner forwards it. When that forward fails
 * (hub unreachable), the strip degrades to its last-known/dimmed state and the
 * rest of the panel — all hub-free — is unaffected.
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
              <span class="fs-lbl">Fleet · read from hub API</span>
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
      color: #ffd9dd;
      font-size: var(--fs-label);
      letter-spacing: 0.08em;
    }
  `,
})
export class LocalInfo {
  protected readonly query = injectRunnerStatusQuery();
  protected readonly fleetQuery = injectRunnerFleetSummaryQuery();

  protected readonly view = computed(() => {
    const data = this.query.data();
    // A malformed body (e.g. `{}` from a misrouted proxy) must render the
    // degraded state, not throw on `hub.endpoint` mid-template.
    return data?.hub && data.capacities && data.pause ? data : null;
  });

  /** The last-known fleet counts, or `null` before the first successful read.
   * TanStack retains this across a later hub-outage error, so the strip keeps
   * showing the last-known numbers (dimmed) rather than blanking. */
  protected readonly fleet = computed(() => this.fleetQuery.data() ?? null);

  /** The hub-summary read failed (hub unreachable / not wired) — the strip
   * degrades to its dimmed last-known state. The rest of the panel is hub-free,
   * so it is unaffected. */
  protected readonly fleetStale = computed<boolean>(() => this.fleetQuery.isError());

  /** The async triad's resolved state — no `'empty'` case: a resolved read
   * with a malformed body renders nothing (the `view()` null-guard in the
   * projected content), the same degraded-blank behavior as before. */
  protected readonly triadState = computed<KitAsyncStateValue>(() => {
    if (this.query.isPending()) return 'loading';
    if (this.query.isError()) return 'error';
    return 'ready';
  });

  /** `-34s` since the last successful PULL, or `never` before first contact. */
  protected readonly lastFlushLabel = computed<string>(() => {
    const contactAt = this.view()?.hub.last_contact_at ?? null;
    if (contactAt === null) return 'never';
    const age = ageMs(contactAt, Date.now());
    return age === null ? '—' : formatAge(age);
  });

  protected readonly lastTickLabel = computed<string>(() => {
    const tickAt = this.view()?.last_tick_at ?? null;
    if (tickAt === null) return '—';
    const age = ageMs(tickAt, Date.now());
    return age === null ? '—' : formatAge(age);
  });
}
