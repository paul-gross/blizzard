import { ChangeDetectionStrategy, Component, TemplateRef, computed, input } from '@angular/core';

import type { ChunkDetail, ChunkUsageTotalView } from '../api/hub';
import { formatCost, formatTokens } from '../cost-format';
import { KitFactList, type KitFact } from '../kit/kit-fact-list';

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
 * The chunk's cost + token-usage breakdown — its own labelled table, separate from
 * {@link ChunkFacts}'s chunk-identity table above it: derived usage/cost is a
 * different kind of information from Status/Node/Runner/Attempts/Graph, so it now
 * reads as its own thing rather than folding into that table as extra rows. The
 * derived total cost is visibly marked PARTIAL when any summed invocation's
 * envelope-less cost was absent (never silently understated), and the chunk's
 * token counts render one labelled row per class — Input, Output, Cache Read,
 * and Cache Creation, the human-readable names for the wire's own
 * `input_tokens`/`output_tokens`/`cache_read_tokens`/`cache_create_tokens`
 * (`ChunkUsageTotalView`, `wire/chunk.py`) — always visible inline, no expand
 * toggle standing between the operator and any of the five figures.
 *
 * Content-projected into {@link ChunkFacts}'s `[token-breakdown]` slot as a block
 * after that component's own `<dl>` closes (not into it), and styled to match: same
 * label/value column widths (`--kv-label-col`, set on `ChunkFacts`'s `:host` and
 * inherited here since projected content still renders inside that host's DOM
 * subtree regardless of component ownership), same fact-row look (`.kv`/`dt`/`dd`).
 * Angular's emulated style encapsulation does not reach across a component
 * boundary, so this table keeps its own copy of the shared `.kv` shape and its
 * `dt`/`dd` rules rather than relying on `ChunkFacts`'s.
 */
@Component({
  selector: 'fleet-chunk-detail-token-breakdown',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitFactList],
  templateUrl: './chunk-token-breakdown.html',
  styleUrl: './chunk-token-breakdown.css',
})
export class ChunkTokenBreakdown {
  /** The chunk aggregate to render (the derived cost/usage total). */
  readonly detail = input.required<ChunkDetail>();

  protected readonly formatCost = formatCost;
  protected readonly formatTokens = formatTokens;

  /** The chunk's derived usage/cost total — never absent: the hub API always
   * populates `cost`, and {@link ZERO_USAGE_TOTAL} covers a construction-site
   * fixture that predates it. Every field here is a required, never-null integer
   * (`ChunkUsageTotalView`, `wire/chunk.py`) — a usage fact's cost can be absent
   * (`cost_partial`), but its token counts cannot, so the four rows below never need
   * their own null handling. */
  protected readonly cost = computed<ChunkUsageTotalView>(() => this.detail().cost ?? ZERO_USAGE_TOTAL);

  /** The usage table's rows — a method, not a stored computed, since each row's
   * markup needs the `<ng-template>` the view declares for it (`KitFactList`'s own
   * templated-row contract). */
  protected factRows(
    costValue: TemplateRef<unknown>,
    inputValue: TemplateRef<unknown>,
    outputValue: TemplateRef<unknown>,
    cacheReadValue: TemplateRef<unknown>,
    cacheCreationValue: TemplateRef<unknown>,
  ): readonly KitFact[] {
    return [
      { label: 'Cost', template: costValue, testid: 'fact-cost' },
      { label: 'Input', template: inputValue, testid: 'fact-tokens-input' },
      { label: 'Output', template: outputValue, testid: 'fact-tokens-output' },
      { label: 'Cache Read', template: cacheReadValue, testid: 'fact-tokens-cache-read' },
      { label: 'Cache Creation', template: cacheCreationValue, testid: 'fact-tokens-cache-creation' },
    ];
  }
}
