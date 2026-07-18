import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { KitAsyncState, type KitAsyncStateValue } from 'fleet';

import { ageMs, formatAge } from './age';
import { injectRunnerStatusQuery } from './status.query';

/**
 * The hub-link panel — the discovery mock's "hub · outbound only, nothing
 * dials in": the configured hub endpoint, derived reachability, last flush
 * (last successful PULL contact), the outbound buffer depth, and this runner's
 * own capacities/pause state. All off `GET /api/runner` — the runner's *own*
 * facts about its hub link, not a live hub read; the board link is the one
 * hand-off to the hub app, minted from the endpoint the wire now carries.
 *
 * The fleet counts strip the mock shows (ready/running/waiting/needs, read
 * from the hub API) is deliberately absent: the hub API allows no cross-origin
 * browser read today, so rendering counts here would mean proxying or CORS —
 * a hub-side decision this panel must not preempt.
 */
@Component({
  selector: 'fleet-local-info',
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
  `,
})
export class LocalInfo {
  protected readonly query = injectRunnerStatusQuery();

  protected readonly view = computed(() => {
    const data = this.query.data();
    // A malformed body (e.g. `{}` from a misrouted proxy) must render the
    // degraded state, not throw on `hub.endpoint` mid-template.
    return data?.hub && data.capacities && data.pause ? data : null;
  });

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
