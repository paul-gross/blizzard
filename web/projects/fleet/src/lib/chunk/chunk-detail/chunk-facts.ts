import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import { NgTemplateOutlet } from '@angular/common';
import { RouterLink } from '@angular/router';

import type { ChunkDetail, ChunkNeighborView, ChunkStatus } from '../../api/hub';
import { STATUS_LANE } from '../../core/chunk-lanes';
import { compactRef } from '../../core/compact-ref';
import { LensLink } from '../../core/lens-link';
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

  protected readonly prerequisites = computed<readonly ChunkNeighborView[]>(() => this.detail().neighborhood?.prerequisites ?? []);

  protected readonly dependents = computed<readonly ChunkNeighborView[]>(() => this.detail().neighborhood?.dependents ?? []);

  protected shortId(chunkId: string): string {
    return compactRef(chunkId);
  }

  /** A neighbor's board lane — read only for a neighbor whose status is known. */
  protected lane(status: ChunkStatus): string {
    return STATUS_LANE(status);
  }
}
