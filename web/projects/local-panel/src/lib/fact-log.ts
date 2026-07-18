import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { compactRef, type runnerApi } from 'fleet';

import { injectRunnerFactsQuery } from './status.query';

/**
 * The local fact log — "runner store": the newest hub-bound facts off the
 * outbound ledger (`GET /api/facts`), newest first. Each row is the fact's
 * kind plus its correlated chunk/lease compact refs and a flush marker —
 * `✓` once the hub acked the seq, `·` while still buffered. A read of the
 * store's own ledger, not a synthesized feed.
 */
@Component({
  selector: 'fleet-fact-log',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="wrap" data-testid="fact-log">
      @if (query.isPending()) {
        <p class="status">LOADING…</p>
      } @else if (query.isError()) {
        <p class="status error">FACT LOG UNAVAILABLE</p>
      } @else if (facts().length === 0) {
        <p class="status" data-testid="facts-empty">NO FACTS RECORDED YET</p>
      } @else {
        @for (fact of facts(); track fact.seq) {
          <div class="ev" data-testid="fact-row" [attr.data-seq]="fact.seq">
            <span class="t">{{ timeLabel(fact) }}</span>
            <span class="flush" [class.acked]="fact.acked_at !== null" [title]="fact.acked_at ? 'flushed to hub' : 'buffered'">
              {{ fact.acked_at !== null ? '✓' : '·' }}
            </span>
            <span class="msg">
              <b class="kind">{{ fact.kind }}</b>
              @if (fact.chunk_id; as chunk) {
                <b class="ref">{{ ref(chunk) }}</b>
              }
              @if (fact.lease_id; as lease) {
                <b class="ref">{{ ref(lease) }}</b>
              }
            </span>
          </div>
        }
      }
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
      min-height: 40px;
    }
    .status {
      position: absolute;
      left: 50%;
      top: 50%;
      transform: translate(-50%, -50%);
      white-space: nowrap;
      color: var(--label-dim);
      font-size: var(--fs-xs);
      letter-spacing: 0.12em;
    }
    .status.error {
      color: var(--red);
    }
    .ev {
      display: flex;
      align-items: baseline;
      gap: 8px;
      padding: 3px 8px;
      border-bottom: 1px solid var(--line);
      font-size: var(--fs-xs);
    }
    .t {
      flex: none;
      color: var(--label-dim);
    }
    .flush {
      flex: none;
      color: var(--label-dim);
    }
    .flush.acked {
      color: var(--green);
    }
    .msg {
      color: var(--label);
      min-width: 0;
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .kind {
      color: var(--text);
      font-weight: normal;
    }
    .ref {
      color: var(--amber);
      font-weight: normal;
      margin-left: 6px;
    }
  `,
})
export class FactLog {
  protected readonly query = injectRunnerFactsQuery();

  protected readonly facts = computed(() => this.query.data() ?? []);

  protected ref(id: string): string {
    return compactRef(id);
  }

  /** `12:41:03` — the fact's UTC clock time; the ledger reads as a tail -f. */
  protected timeLabel(fact: runnerApi.FactView): string {
    const parsed = Date.parse(fact.created_at);
    if (Number.isNaN(parsed)) return '—';
    return new Date(parsed).toISOString().slice(11, 19);
  }
}
