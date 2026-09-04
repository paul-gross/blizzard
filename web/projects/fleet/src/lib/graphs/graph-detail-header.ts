import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitButton } from '../kit/kit-button';

/**
 * The graph detail panel's own header content — the lifecycle text, graph id,
 * and (when `canEdit`) the retire/re-enable control, supplementing
 * `fleet-kit-panel`'s `label` (bound to the graph's name directly in
 * `graph-detail.html`) in its header bar. Projected into `KitPanel`'s
 * `fleetKitPanelHeader` slot in supplement mode: `:host`'s `display: contents`
 * (mirroring `MachineDetailHeader`'s own convention) puts every span/button
 * directly alongside the panel's own `.lbl` as flex items of its `.p-hdr`,
 * painting no chrome of its own beyond that content (`bzh:frontend-kit-floor`).
 *
 * The retire/re-enable control lives here — not in `GraphDetailLifecycle` —
 * so it sits on the same row as the lifecycle text and can right-align
 * against it (`graph-detail-header.css`'s `margin-left: auto`), rather than
 * stranded in the panel body below the diagram. It owns the confirm-then-emit
 * pattern itself, mirroring `chunk-detail-header.ts`'s pause/detach/complete
 * controls — the mutation stays in `GraphDetail` (`bzh:frontend-container-
 * presentational`). The action-error line and the entry-node line stay
 * `GraphDetailLifecycle`'s, rendered as ordinary body content below the
 * header bar.
 */
@Component({
  selector: 'fleet-graph-detail-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton],
  templateUrl: './graph-detail-header.html',
  styleUrl: './graph-detail-header.css',
})
export class GraphDetailHeader {
  readonly graphId = input.required<string>();
  readonly retired = input.required<boolean>();

  /** Whether the current identity may author graphs (`graph:edit`, admin-tier — issue
   * #93) — gates the retire/re-enable control. */
  readonly canEdit = input(false);

  /** Emitted with the graph id once the operator confirms Retire. */
  readonly retire = output<string>();

  /** Emitted with the graph id once the operator confirms Enable. */
  readonly enable = output<string>();

  /** Confirm, then emit `retire` for the container's mutation to fire (issue #101). */
  protected onRetire(): void {
    const confirmed = globalThis.confirm(
      `Retire graph ${this.graphId()}? It is excluded from name resolution and refuses new ` +
        `re-pins; any chunk already running on it is left to run out.`,
    );
    if (!confirmed) return;
    this.retire.emit(this.graphId());
  }

  /** Confirm, then emit `enable` for the container's mutation to fire (issue #101). */
  protected onEnable(): void {
    const confirmed = globalThis.confirm(
      `Re-enable graph ${this.graphId()}? It resumes normal newest-per-name derivation.`,
    );
    if (!confirmed) return;
    this.enable.emit(this.graphId());
  }
}
