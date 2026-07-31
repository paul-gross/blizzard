import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

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
 * The chunk's cost + token-usage breakdown (issue #79, issue #60, issue #182) — the
 * derived total cost (visibly marked PARTIAL when any summed invocation's
 * envelope-less cost was absent — never silently understated) and the chunk's
 * token counts by class, always visible inline (no expand toggle, issue #182).
 *
 * Content-projected into {@link ChunkFacts}'s `[token-breakdown]` slot, so
 * these two `<dt>`/`<dd>` pairs render as rows of the same `<dl class="kv">`
 * the facts component owns — `:host { display: contents }` keeps this
 * component out of the grid's box tree so its `dt`/`dd` children are direct
 * grid items, exactly as the monolith rendered them. Angular's emulated style
 * encapsulation does not reach across that projection boundary — the `dt`/`dd`
 * elements below are this component's own template output, so `ChunkFacts`'s
 * `.kv dt`/`.kv dd` rules never match them. This component keeps its own copy
 * of those rules (issue #182) so the projected rows read identically to their
 * siblings instead of falling back to the browser's default `dt`/`dd` styling.
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
    <dd data-testid="fact-tokens">{{ tokensLine() }}</dd>
  `,
  styles: `
    :host {
      display: contents;
    }
    /* This component's own copy of ChunkFacts's .kv dt/.kv dd rules (chunk-facts.ts)
       — see the class doc comment for why the parent's rules can't reach these rows. */
    dt {
      color: var(--label);
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      text-transform: uppercase;
      align-self: center;
    }
    dd {
      margin: 0;
      color: var(--amber);
      overflow-wrap: anywhere;
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
  `,
})
export class ChunkTokenBreakdown {
  /** The chunk aggregate to render (the derived cost/usage total, issue #60). */
  readonly detail = input.required<ChunkDetail>();

  protected readonly formatCost = formatCost;

  /** The chunk's derived usage/cost total (issue #60) — never absent: the hub API
   * always populates `cost`, and {@link ZERO_USAGE_TOTAL} covers a construction-site
   * fixture that predates it. */
  protected readonly cost = computed<ChunkUsageTotalView>(() => this.detail().cost ?? ZERO_USAGE_TOTAL);

  /** The chunk's token counts by class, always visible inline (issue #182) — no
   * expand toggle standing between the operator and any of the four figures. */
  protected readonly tokensLine = computed<string>(() => {
    const c = this.cost();
    return (
      `${formatTokens(c.input_tokens)} I, ${formatTokens(c.output_tokens)} O, ` +
      `${formatTokens(c.cache_read_tokens)} CR, ${formatTokens(c.cache_create_tokens)} CC`
    );
  });
}
