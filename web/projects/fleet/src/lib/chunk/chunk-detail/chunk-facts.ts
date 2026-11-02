import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { RouterLink } from '@angular/router';

import type { ChunkDetail, ChunkNeighborView } from '../../api/hub';
import { STATUS_LANE, UNKNOWN_LANE } from '../../core/chunk-lanes';
import { compactRef } from '../../core/compact-ref';
import { LensLink } from '../../core/lens-link';
import { type SessionEfficiencyInput } from '../../core/session-efficiency';
import { SessionEfficiency } from '../../core/session-efficiency-view';
import { LaneIcon } from '../chunk-card/lane-icon';

/**
 * The chunk's work-item facts box: its total session efficiency cost, then — only where the
 * chunk has standing edges — its Depends on / Blocks neighbors side by side, each with the
 * board's own lane icon for its status and linked to its chunk.
 */
@Component({
  selector: 'fleet-chunk-detail-facts',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [LaneIcon, LensLink, NgTemplateOutlet, RouterLink, SessionEfficiency],
  templateUrl: './chunk-facts.html',
  styleUrl: './chunk-facts.css',
})
export class ChunkFacts {
  readonly detail = input.required<ChunkDetail>();

  /** The chunk detail route's own path segments, before the chunk id — each neighbor links there. */
  readonly chunkLinkBase = input<readonly string[]>(['/board', 'chunk']);

  /** The chunk's summed usage, in the session efficiency cost's own shape. */
  protected readonly usage = computed<SessionEfficiencyInput>(() => {
    const c = this.detail().cost;
    return {
      costUsd: c?.cost_usd ?? 0,
      estimatedCostUsd: c?.estimated_cost_usd ?? null,
      costPartial: c?.cost_partial ?? false,
      inputTokens: c?.input_tokens ?? 0,
      outputTokens: c?.output_tokens ?? 0,
      cacheReadTokens: c?.cache_read_tokens ?? 0,
      cacheCreateTokens: c?.cache_create_tokens ?? 0,
    };
  });


  protected readonly prerequisites = computed<readonly ChunkNeighborView[]>(() => this.detail().neighborhood?.prerequisites ?? []);

  protected readonly dependents = computed<readonly ChunkNeighborView[]>(() => this.detail().neighborhood?.dependents ?? []);

  protected shortId(chunkId: string): string {
    return compactRef(chunkId);
  }

  /** The neighbor's board lane; a neighbor with no known status reads {@link UNKNOWN_LANE}, never a guessed one. */
  protected lane(n: ChunkNeighborView): string {
    return n.status ? STATUS_LANE(n.status) : UNKNOWN_LANE;
  }
}
