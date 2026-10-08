import { ChangeDetectionStrategy, Component, input, output, signal } from '@angular/core';

import { KitBadge, KitButton, KitConfirmDialog } from 'fleet';

import { lifecycleTone } from '../core/lifecycle-tone';

/**
 * The graph detail panel's header content — the lifecycle text, graph id, and
 * (when `canEdit`) the retire/re-enable control — projected into `KitPanel`'s
 * `fleetKitPanelHeader` slot in supplement mode. `:host`'s `display: contents` puts
 * every span and button directly into the header row as flex items, painting no
 * chrome of its own (`bzh:frontend-kit-floor`).
 *
 * The retire/re-enable control sits here so it shares the lifecycle text's row and
 * right-aligns against it. It confirms, then emits; it fires no mutation
 * (`bzh:frontend-container-presentational`).
 */
@Component({
  selector: 'app-graph-detail-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitBadge, KitButton, KitConfirmDialog],
  templateUrl: './graph-detail-header.html',
  styleUrl: './graph-detail-header.css',
})
export class GraphDetailHeader {
  readonly graphId = input.required<string>();
  readonly retired = input.required<boolean>();

  /** Whether the current identity may author graphs (`graph:edit`, admin-tier)
   * — gates the retire/re-enable control. */
  readonly canEdit = input(false);

  /** Whether the retire/enable mutation is in flight — disables both Retire and
   * Enable, since only one is ever shown for the graph's current lifecycle state and
   * there is only ever one lifecycle mutation in flight for one graph at a time. */
  readonly lifecyclePending = input(false);

  /** The lifecycle badge's rendered value — which may be a pending retire/enable's
   * predicted `retired` flag rather than the real {@link retired}
   * (`bzh:frontend-pending-override`). Which of Retire/Enable
   * renders stays keyed off the real {@link retired} so an in-flight mutation's own
   * predicted outcome cannot flip which control the next click would fire. */
  readonly renderedRetired = input.required<boolean>();

  /** Emitted with the graph id once the operator confirms Retire. */
  readonly retire = output<string>();

  /** Emitted with the graph id once the operator confirms Enable. */
  readonly enable = output<string>();

  /** The lifecycle badge's tone, from the hub's one lifecycle owner. */
  protected readonly lifecycleTone = lifecycleTone;

  protected readonly pendingConfirm = signal<{
    readonly heading: string;
    readonly message: string;
    readonly confirmLabel: string;
    readonly variant: 'primary' | 'danger';
    readonly run: () => void;
  } | null>(null);

  /** Open a confirmation, and emit `retire` once the operator confirms. */
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

  /** Open a confirmation, and emit `enable` once the operator confirms. */
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
