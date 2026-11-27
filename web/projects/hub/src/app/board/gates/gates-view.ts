import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { compactRef, runnerDisplayName } from 'fleet/core';
import { KitAsyncState, type KitAsyncStateValue, KitPanel } from 'fleet/kit';
import { KitSelectList, KitSelectRow } from 'fleet/kit/select-list';
import type { BoardDecisionPresentation } from './gates.domain';

/**
 * The open-gates rail's presentational half — one row per open decision,
 * naming its chunk, node, origin, and choices, with a click-to-open row.
 * Renders exactly the gates it is handed; injects no query.
 *
 * Its test handles are `rail-gate`-prefixed because the chunk detail dock renders
 * the same chunk's gate at the same time under `open-decision`; a shared handle
 * would make a browser locator match both.
 */
@Component({
  selector: 'app-gates-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitPanel, KitSelectList, KitSelectRow],
  templateUrl: './gates-view.html',
  styleUrl: './gates-view.css',
})
export class GatesPanelView {
  /** Each runner's host name by runner id — a runner the map omits shows no host. */
  readonly runnerHosts = input<ReadonlyMap<string, string>>(new Map());

  /** Every open gate across the fleet. */
  readonly gates = input.required<readonly BoardDecisionPresentation[]>();

  /** The decisions query's async state. */
  readonly state = input.required<KitAsyncStateValue>();

  /** Emitted with the gate's chunk id when a gate is activated. */
  readonly selectChunk = output<string>();

  /** The list's `value`: a rail opens a chunk rather than holding a selection, so no row is ever the open one. */
  protected readonly noRowOpen = null as string | null;

  /** A row's value is its own id (one chunk can hold several), so open the chunk it belongs to. */
  protected open(id: string): void {
    const row = this.gates().find((r) => r.decision_id === id);
    if (row !== undefined) this.selectChunk.emit(row.chunk_id);
  }

  protected shortId(chunkId: string): string {
    return compactRef(chunkId);
  }

  /** Who imposed the gate: the chunk's graph, or the runner that added it. */
  protected origin(gate: BoardDecisionPresentation): string {
    return gate.imposed_by_runner_id
      ? `runner ${runnerDisplayName(gate.imposed_by_runner_id, gate.imposed_by_runner_name, this.runnerHosts().get(gate.imposed_by_runner_id))}`
      : 'graph';
  }

  protected choices(gate: BoardDecisionPresentation): string {
    return gate.choices.join(' · ');
  }
}
