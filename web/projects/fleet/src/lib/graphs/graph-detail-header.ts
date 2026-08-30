import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitButton } from '../kit/kit-button';

/**
 * The graph detail's **header** — the identity row (name, lifecycle badge, graph id),
 * the `canEdit`-gated retire/re-enable controls (issue #101, gated on `graph:edit` —
 * issue #93), the action-error line (issue #42's report-don't-swallow pattern), and the
 * entry-node line.
 *
 * Presentational only: owns the confirmation for retire/enable and emits `retire`/
 * `enable` only once confirmed — mirrors `chunk-detail-header.ts`'s confirm-then-emit
 * pattern for pause/detach — but the mutation itself stays in {@link GraphDetail}
 * (`bzh:frontend-container-presentational`).
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
  readonly name = input.required<string>();
  readonly retired = input.required<boolean>();

  /** Whether the current identity may author graphs (`graph:edit`, admin-tier — issue
   * #93) — gates the retire/re-enable controls. */
  readonly canEdit = input(false);

  /** Set on a failed retire/enable (issue #42's report-don't-swallow pattern), or
   * `null` between attempts. */
  readonly actionError = input<string | null>(null);

  readonly entryNodeName = input.required<string>();

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
