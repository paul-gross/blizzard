import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';

import type { ChunkDetail, ChunkUsageTotalView } from '../api/hub';
import { formatCost, formatTokens } from '../cost-format';

/** The all-zero, non-partial total — this component's default before `detail().cost`
 * carries a real read (mirrors the hub's own `_zero_usage_total`, `wire/chunk.py`). */
const ZERO_USAGE_TOTAL: ChunkUsageTotalView = {
  input_tokens: 0,
  output_tokens: 0,
  cache_read_tokens: 0,
  cache_create_tokens: 0,
  cost_usd: 0,
  cost_partial: false,
};

/**
 * The chunk's cost + token-usage breakdown (issue #79, issue #60) — the
 * derived total cost (visibly marked PARTIAL when any summed invocation's
 * envelope-less cost was absent — never silently understated) and the
 * chunk-total token count, expandable into its per-class breakdown.
 *
 * Content-projected into {@link ChunkFacts}'s `[token-breakdown]` slot, so
 * these two `<dt>`/`<dd>` pairs render as rows of the same `<dl class="kv">`
 * the facts component owns — `:host { display: contents }` keeps this
 * component out of the grid's box tree so its `dt`/`dd` children are direct
 * grid items, exactly as the monolith rendered them.
 */
@Component({
  selector: 'fleet-chunk-detail-token-breakdown',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <dt>Cost</dt>
    <dd data-testid="fact-cost">
      <span data-testid="cost-total-usd">{{ formatCost(cost().cost_usd, cost().cost_partial) }}</span>
      @if (cost().cost_partial) {
        <span
          class="partial-badge"
          data-testid="cost-partial-badge"
          title="At least one invocation's cost was absent (a crash/reap-path exit) — this total is a lower bound, not the true spend."
          >PARTIAL</span
        >
      }
    </dd>
    <dt>Tokens</dt>
    <dd data-testid="fact-tokens">
      <button
        type="button"
        class="tok-toggle"
        data-testid="tokens-expand-toggle"
        [attr.aria-label]="(tokensExpanded() ? 'Collapse' : 'Expand') + ' token breakdown'"
        (click)="tokensExpanded.set(!tokensExpanded())"
      >
        <span class="caret">{{ tokensExpanded() ? '▾' : '▸' }}</span>
        <span data-testid="tokens-total">{{ formatTokens(totalTokens()) }}</span>
      </button>
      @if (tokensExpanded()) {
        <dl class="kv tok-breakdown" data-testid="tokens-breakdown">
          <dt>Input</dt>
          <dd data-testid="tokens-input">{{ formatTokens(cost().input_tokens) }}</dd>
          <dt>Output</dt>
          <dd data-testid="tokens-output">{{ formatTokens(cost().output_tokens) }}</dd>
          <dt>Cache read</dt>
          <dd data-testid="tokens-cache-read">{{ formatTokens(cost().cache_read_tokens) }}</dd>
          <dt>Cache create</dt>
          <dd data-testid="tokens-cache-create">{{ formatTokens(cost().cache_create_tokens) }}</dd>
        </dl>
      }
    </dd>
  `,
  styles: `
    :host {
      display: contents;
    }
    /* The PARTIAL badge marks a cost total whose sum is a lower bound (issue #60) —
       never silently understated. */
    .partial-badge {
      margin-left: 4px;
      padding: 0 4px;
      border: 1px solid var(--red-dim);
      color: var(--red);
      font-size: var(--fs-label);
      letter-spacing: 0.1em;
      cursor: help;
    }
    .tok-toggle {
      border: 0;
      background: transparent;
      padding: 0;
      color: var(--amber);
      font: inherit;
      cursor: pointer;
    }
    .tok-toggle .caret {
      color: var(--label-dim);
      margin-right: 3px;
    }
    /* A nested "dl.kv" — this component's own style scope needs its own copy of
       the grid rules ChunkFacts owns for the outer one. */
    .kv {
      display: grid;
      grid-template-columns: 74px 1fr;
      gap: 2px 8px;
      font-size: var(--fs-sm);
    }
    .kv dt {
      color: var(--label);
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      text-transform: uppercase;
      align-self: center;
    }
    .kv dd {
      margin: 0;
      color: var(--amber);
      overflow-wrap: anywhere;
    }
    .tok-breakdown {
      margin-top: 3px;
      grid-template-columns: 84px 1fr;
    }
  `,
})
export class ChunkTokenBreakdown {
  /** The chunk aggregate to render (the derived cost/usage total, issue #60). */
  readonly detail = input.required<ChunkDetail>();

  protected readonly formatCost = formatCost;
  protected readonly formatTokens = formatTokens;

  /** The chunk's derived usage/cost total (issue #60) — never absent: the hub API
   * always populates `cost`, and {@link ZERO_USAGE_TOTAL} covers a construction-site
   * fixture that predates it. */
  protected readonly cost = computed<ChunkUsageTotalView>(() => this.detail().cost ?? ZERO_USAGE_TOTAL);

  /** The chunk-total token count across every class — the collapsed reading. */
  protected readonly totalTokens = computed<number>(() => {
    const c = this.cost();
    return c.input_tokens + c.output_tokens + c.cache_read_tokens + c.cache_create_tokens;
  });

  /** Whether the token total is broken out by class. Collapsed by default — the
   * chunk-facts column stays scannable until the operator asks for the detail. */
  protected readonly tokensExpanded = signal(false);
}
