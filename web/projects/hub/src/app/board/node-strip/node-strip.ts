import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { injectHubChunkDetailQuery } from 'fleet';
import { injectHubGraphQuery } from '../../graphs/graphs.query';
import { isDenseStrip, stripGroups } from './node-strip.model';

/**
 * A chunk's progress through its graph: one block per node, start to finish, each lit by the chunk's last visit
 * there — green passed, red failed, dark unvisited — and the node it stands on pulsing in the fleet registry's
 * overload cyan. Shared by the board's card and list row.
 *
 * Reads the chunk's graph (cached per graph) and its detail (the same entry the detail dock reads) itself, so the
 * card and row that host it stay presentational; draws nothing until both are read.
 */
@Component({
  selector: 'app-node-strip',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './node-strip.html',
  styleUrl: './node-strip.css',
  host: { '[class.dense]': 'dense()' },
})
export class NodeStrip {
  readonly chunkId = input.required<string>();
  readonly graphId = input.required<string | null>();
  readonly currentNodeId = input<string | null>(null);

  private readonly graph = injectHubGraphQuery(() => this.graphId());
  private readonly detail = injectHubChunkDetailQuery(() => this.chunkId());

  protected readonly groups = computed(() => stripGroups(this.graph.data(), this.detail.data(), this.currentNodeId()));

  /** Past twenty blocks the strip packs tighter, so each still reads. */
  protected readonly dense = computed(() => isDenseStrip(this.groups()));
}
