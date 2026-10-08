import { ChangeDetectionStrategy, Component, TemplateRef, computed, input } from '@angular/core';

import type { ChunkDetail, ChunkUsageTotalView } from '../../api/hub';
import { formatCost, formatTokens, TOTAL_COST_PARTIAL_TITLE } from '../../core/cost-format';
import { KitFactList, type KitFact } from '../../kit/kit-fact-list';

/** The all-zero, non-partial total — this component's default before `detail().cost`
 * carries a real read. */
const ZERO_USAGE_TOTAL: ChunkUsageTotalView = {
  input_tokens: 0,
  output_tokens: 0,
  cache_read_tokens: 0,
  cache_create_tokens: 0,
  cost_usd: 0,
  cost_partial: false,
};

/**
 * The chunk's cost and token-usage table: one cost row, marked partial whenever
 * `cost_partial` is set, and one row per token class, all visible inline.
 *
 * Emulated style encapsulation does not cross a component boundary, so this table
 * keeps its own copy of the `.kv` shape rather than inheriting one.
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

  protected readonly totalCostPartialTitle = TOTAL_COST_PARTIAL_TITLE;
  protected readonly formatCost = formatCost;
  protected readonly formatTokens = formatTokens;

  /** The chunk's derived usage/cost total, or {@link ZERO_USAGE_TOTAL} before a real read. */
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
