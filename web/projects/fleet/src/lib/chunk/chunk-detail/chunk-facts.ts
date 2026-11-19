import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { RouterLink } from '@angular/router';

import { STATUS_LANE, type LaneStatus } from '../../core/chunk-lanes';
import { compactRef } from '../../core/compact-ref';
import type { SessionEfficiencyInput } from '../../core/session-efficiency';
import { AppLink, AppPath } from '../../core/app-scope';
import { SessionEfficiency } from '../../core/session-efficiency-view';
import { LaneIcon } from '../chunk-card/lane-icon';

export interface FactsNeighbor { readonly chunk_id: string; readonly laneStatus: LaneStatus | null; }
export interface FactsDetail {
  readonly cost: SessionEfficiencyInput;
  readonly neighborhood: { readonly prerequisites: readonly FactsNeighbor[]; readonly dependents: readonly FactsNeighbor[] } | null;
}

/**
 * The chunk's work-item facts box: its total session efficiency cost, then — only where the
 * chunk has standing edges — its Depends on / Blocks neighbors side by side, each with the
 * board's own lane icon for its status and linked to its chunk.
 */
@Component({
  selector: 'fleet-chunk-detail-facts',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [AppLink, AppPath, LaneIcon, NgTemplateOutlet, RouterLink, SessionEfficiency],
  templateUrl: './chunk-facts.html',
  styleUrl: './chunk-facts.css',
})
export class ChunkFacts {
  readonly detail = input.required<FactsDetail>();

  /** The chunk detail route's own path segments, before the chunk id — each neighbor links there. */
  readonly chunkLinkBase = input<readonly string[]>(['/board', 'chunk']);

  /** Neighbors rendered in the current board, eligible for selection in place. */
  readonly boardChunkIds = input<ReadonlySet<string>>(new Set());

  protected readonly prerequisites = computed<readonly FactsNeighbor[]>(() => this.detail().neighborhood?.prerequisites ?? []);

  protected readonly dependents = computed<readonly FactsNeighbor[]>(() => this.detail().neighborhood?.dependents ?? []);

  protected shortId(chunkId: string): string {
    return compactRef(chunkId);
  }

  /** A neighbor's board lane — read only for a neighbor whose status is known. */
  protected lane(status: LaneStatus): string {
    return STATUS_LANE(status.kind);
  }
}
