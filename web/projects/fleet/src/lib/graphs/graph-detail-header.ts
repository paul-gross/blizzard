import { ChangeDetectionStrategy, Component, input, output, signal } from '@angular/core';

import { KitButton } from '../kit/kit-button';
import { KitConfirmDialog } from '../kit/kit-confirm-dialog';

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
  imports: [KitButton, KitConfirmDialog],
  templateUrl: './graph-detail-header.html',
  styleUrl: './graph-detail-header.css',
})
export class GraphDetailHeader {
  readonly graphId = input.required<string>();
  readonly retired = input.required<boolean>();

  /** Whether the current identity may author graphs (`graph:edit`, admin-tier — issue
   * #93) — gates the retire/re-enable control. */
  readonly canEdit = input(false);

  /** Whether the retire/enable mutation is in flight — disables both Retire and
   * Enable, since only one is ever shown for the graph's current lifecycle state and
   * there is only ever one lifecycle mutation in flight for one graph at a time. */
  readonly lifecyclePending = input(false);

  /** The lifecycle badge's rendered value — the container's own applied result
   * (`bzh:frontend-pending-override`, `graph-detail.ts`'s `renderedRetired`): the
   * `retired` flag a currently pending retire/enable predicts, already merged with
   * the real {@link retired} where nothing overrides it. Which of Retire/Enable
   * renders stays keyed off the real {@link retired} so an in-flight mutation's own
   * predicted outcome cannot flip which control the next click would fire. */
  readonly renderedRetired = input.required<boolean>();

  /** Emitted with the graph id once the operator confirms Retire. */
  readonly retire = output<string>();

  /** Emitted with the graph id once the operator confirms Enable. */
  readonly enable = output<string>();

  protected readonly pendingConfirm = signal<{
    readonly heading: string;
    readonly message: string;
    readonly confirmLabel: string;
    readonly variant: 'primary' | 'danger';
    readonly run: () => void;
  } | null>(null);

  /** Open a confirmation before emitting `retire` for the container's mutation to fire (issue #101). */
  protected onRetire(): void {
    const graphId = this.graphId();
    this.pendingConfirm.set({
      heading: `Retire graph ${graphId}`,
      message: `Retire graph ${graphId}? It is excluded from name resolution and refuses new ` +
        `re-pins; any chunk already running on it is left to run out.`,
      confirmLabel: 'Retire',
      variant: 'danger',
      run: () => this.retire.emit(graphId),
    });
  }

  /** Open a confirmation before emitting `enable` for the container's mutation to fire (issue #101). */
  protected onEnable(): void {
    const graphId = this.graphId();
    this.pendingConfirm.set({
      heading: `Re-enable graph ${graphId}`,
      message: `Re-enable graph ${graphId}? It resumes normal newest-per-name derivation.`,
      confirmLabel: 'Re-enable',
      variant: 'primary',
      run: () => this.enable.emit(graphId),
    });
  }

  protected onConfirmed(): void {
    const pending = this.pendingConfirm();
    this.pendingConfirm.set(null);
    pending?.run();
  }

  protected onCancelled(): void {
    this.pendingConfirm.set(null);
  }
}
