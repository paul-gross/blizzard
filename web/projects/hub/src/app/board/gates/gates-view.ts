import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { type DecisionView, compactRef, runnerDisplayName, KitAsyncState, type KitAsyncStateValue, KitPanel } from 'fleet';

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
  imports: [KitAsyncState, KitPanel],
  templateUrl: './gates-view.html',
  styleUrl: './gates-view.css',
})
export class GatesPanelView {
  /** Every open gate across the fleet. */
  readonly gates = input.required<readonly DecisionView[]>();

  /** The decisions query's async state. */
  readonly state = input.required<KitAsyncStateValue>();

  /** Emitted with a chunk id when a gate is activated — opens it in the detail panel. */
  readonly selectChunk = output<string>();

  protected shortId(chunkId: string): string {
    return compactRef(chunkId);
  }

  /** Who imposed the gate: the chunk's graph, or the runner that added it. */
  protected origin(gate: DecisionView): string {
    return gate.imposed_by_runner_id
      ? `runner ${runnerDisplayName(gate.imposed_by_runner_id, gate.imposed_by_runner_name)}`
      : 'graph';
  }

  protected choices(gate: DecisionView): string {
    return (gate.choices ?? []).map((choice) => choice.name).join(' · ');
  }
}
