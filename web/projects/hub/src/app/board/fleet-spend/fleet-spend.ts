import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { SessionEfficiency, type FleetSpendView, type SessionEfficiencyInput } from 'fleet';

/** A spend read as the session efficiency figure's input — the same cost, token classes and estimate flags. */
export function spendEfficiency(s: FleetSpendView): SessionEfficiencyInput {
  return {
    costUsd: s.cost_usd,
    estimatedCostUsd: s.estimated_cost_usd,
    costPartial: s.cost_partial,
    inputTokens: s.input_tokens,
    outputTokens: s.output_tokens,
    cacheReadTokens: s.cache_read_tokens,
    cacheCreateTokens: s.cache_create_tokens,
  };
}

/**
 * The fleet's spend as two rows, Today and Yesterday, each the session efficiency figure — the cost (`formatCost`, so
 * an estimate reads `~$X` and a partial window `$X+`), then the distinct input, output and cache miss, spelled out on
 * hover. A fixed label column lines the figures up. Presentational; the Yesterday row waits for its read.
 */
@Component({
  selector: 'app-fleet-spend',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [SessionEfficiency],
  host: { '[class.stacked]': "layout() === 'stacked'" },
  templateUrl: './fleet-spend.html',
  styleUrl: './fleet-spend.css',
})
export class FleetSpend {
  readonly today = input.required<FleetSpendView>();
  readonly yesterday = input<FleetSpendView | null>(null);

  /** `columns`: a fixed label column beside each figure; `stacked`: each label on its own line over its figure. */
  readonly layout = input<'columns' | 'stacked'>('columns');

  protected readonly efficiency = spendEfficiency;
}
